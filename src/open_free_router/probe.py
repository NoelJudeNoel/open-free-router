#!/usr/bin/env python3
"""Live usability verification for free-model funnels ("probe-then-trust").

2026-09-02 audit finding that motivated this module: a /v1/models listing
is necessary but NOT sufficient. Empirical evidence from probing every
chat-capable entry of that day's catalogs with one minimal chat completion:

- NVIDIA NIM listed 54 chat-capable models; only 9 returned 200. The rest
  404'd with "Function ... Not found" -- including ``moonshotai/kimi-k2.6``,
  a model that sat in our own production registry as healthy. The catalog
  entry had outlived the actual deployment.
- SenseNova listed 8 free models; 2 of the new ones (sensenova-u1-fast,
  sensenova-u1.5-lite) 404'd on the very first real request.

So every refresh source's structural free-detection (allowlist / pricing /
``-free`` suffix) is now followed by one real probe per candidate model
here, and only models that answer are allowed into the registry.

Verdicts
--------
verified      HTTP 200 -- actually usable, keep.
alive         429 / 5xx -- model exists but is saturated right now; keep
              (tier failover already absorbs transient 429/503 at request
              time, so a rate-limited model is still a useful pool member).
dead          400 / 404 -- drop. 400 gets one retry with a fuller payload
              first: strict upstreams (e.g. StepFun via Nous) reject tiny
              max_tokens, which is a payload problem, not a dead model.
auth          401 / 403 -- key/account-level. A per-model verdict is void
              in this state.
inconclusive  network error / timeout / unexpected status -- keep,
              conservatively.

Batch guards (never wipe the registry because of an outage):
- If every result in a batch is auth/inconclusive, the whole batch is
  inconclusive: keep the old list untouched.
- A run of MAX_CONSECUTIVE_INCONCLUSIVE timeouts trips a circuit breaker:
  the remaining models are marked inconclusive without being probed, so a
  hung upstream can't turn one refresh cycle into a timeout * N stall.
"""
from __future__ import annotations

import functools
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests

from open_free_router.registry import ModelInfo

# Providers whose OpenAI-compatible chat endpoint is not
# <upstream_url>/chat/completions.
CHAT_PATH_OVERRIDES: dict[str, str] = {
    # Google AI Studio's OpenAI-compat surface lives one level below v1beta
    "google-ai-studio": "/openai/chat/completions",
}

# Providers whose relay gates on origin and requires a session-affinity
# header on the probe request too (see upstream.SESSION_HEADER_PROVIDERS).
# OpenCode Zen (2026-09-05): without this header the probe 403s
# FreeTierError on every Zen model, which the keep-all guard would NOT
# catch if even one model bypasses the gate (it did -- space-bunny-free --
# so the rest got wiped to a single survivor). The probe sends one stable
# value; per-conversation granularity is irrelevant for a one-shot probe.
PROBE_SESSION_HEADERS: dict[str, dict[str, str]] = {
    "opencode-zen-free": {"x-opencode-session": "ofr-probe"},
}

DEFAULT_TIMEOUT = 30
DEFAULT_DELAY = 0.2
MAX_CONSECUTIVE_INCONCLUSIVE = 5

VERIFIED = "verified"
ALIVE = "alive"
DEAD = "dead"
AUTH = "auth"
INCONCLUSIVE = "inconclusive"


@dataclass(slots=True)
class ProbeResult:
    model: str
    status: int
    verdict: str
    detail: str = ""

    @property
    def kept(self) -> bool:
        return self.verdict in (VERIFIED, ALIVE, INCONCLUSIVE)


def _classify(status: int) -> str:
    if status == 200:
        return VERIFIED
    if status == 429 or 500 <= status <= 599:
        return ALIVE
    if status in (400, 404):
        return DEAD
    if status in (401, 403):
        return AUTH
    return INCONCLUSIVE


def probe_chat(
    base_url: str,
    api_key: str,
    model: str,
    chat_path: str = "/chat/completions",
    timeout: int = DEFAULT_TIMEOUT,
    max_tokens: int = 16,
    session: Optional[requests.Session] = None,
    extra_headers: Optional[dict] = None,
) -> ProbeResult:
    """Send one minimal chat completion to `model` and classify the answer.

    The payload is deliberately tiny (single "Say pong" user turn, small
    max_tokens) so a full-provider sweep costs seconds, not tokens.
    `extra_headers` injects provider-specific headers (e.g. Zen's
    x-opencode-session) so origin-gated relays don't false-negative.
    """
    url = f"{base_url.rstrip('/')}{chat_path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "open-free-router/0.1",
        "Content-Type": "application/json",
    }
    if extra_headers:
        for k, v in extra_headers.items():
            headers[k] = str(v)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Say pong"}],
        "max_tokens": max_tokens,
        "stream": False,
    }
    try:
        r = (session or requests).post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as e:
        return ProbeResult(model=model, status=0, verdict=INCONCLUSIVE,
                           detail=f"{type(e).__name__}: {e}")

    verdict = _classify(r.status_code)

    # 400 is ambiguous: strict upstreams reject small max_tokens or minimal
    # payloads. One retry with a fuller payload separates "dead model" from
    # "picky payload" before we drop anything.
    if verdict == DEAD and r.status_code == 400:
        retry_payload = dict(payload, max_tokens=256)
        retry_payload["messages"] = [
            {"role": "user", "content": "Hello! Please reply with the word: pong"},
        ]
        try:
            r2 = (session or requests).post(url, headers=headers, json=retry_payload, timeout=timeout)
            verdict = _classify(r2.status_code)
            r = r2
        except requests.RequestException as e:
            return ProbeResult(model=model, status=400, verdict=INCONCLUSIVE,
                               detail=f"400, retry exc {type(e).__name__}")

    detail = ""
    try:
        j = r.json()
        detail = (j.get("error") or {}).get("message", "") if not r.ok else ""
    except ValueError:
        detail = r.text[:120]
    return ProbeResult(model=model, status=r.status_code, verdict=verdict,
                       detail=detail.replace("\n", " ")[:160])


def verify_models(
    provider_name: str,
    base_url: str,
    api_key: str,
    models: List[ModelInfo],
    chat_path: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    delay: float = DEFAULT_DELAY,
    probe_fn=None,
) -> Tuple[List[ModelInfo], List[ProbeResult]]:
    """Probe every candidate model; return (kept_models, results).

    Kept order preserves the input order (fetch() output ordering is
    meaningful to refresh()'s change detection). Batch guards:

    - all-auth / all-inconclusive -> keep everything (account- or network-
      level failure says nothing about individual models);
    - circuit breaker on consecutive timeouts (MAX_CONSECUTIVE_INCONCLUSIVE).
    """
    if not models:
        return [], []

    if chat_path is None:
        chat_path = CHAT_PATH_OVERRIDES.get(provider_name, "/chat/completions")
    if probe_fn is None:
        probe_fn = probe_chat
    # Origin-gated providers (OpenCode Zen) need a session-affinity header on
    # the probe too, or every candidate 403s FreeTierError. Bake it into the
    # real probe_chat via partial; a caller-supplied probe_fn is left alone
    # (its signature doesn't accept extra_headers and tests rely on that).
    extra = PROBE_SESSION_HEADERS.get(provider_name)
    if extra and probe_fn is probe_chat:
        probe_fn = functools.partial(probe_fn, extra_headers=extra)

    results: List[ProbeResult] = []
    consecutive_inconclusive = 0
    for i, m in enumerate(models):
        if consecutive_inconclusive >= MAX_CONSECUTIVE_INCONCLUSIVE:
            results.append(ProbeResult(model=m.effective_upstream_id or m.id, status=0,
                                       verdict=INCONCLUSIVE,
                                       detail="circuit breaker: skipped after repeated timeouts"))
            continue
        res = probe_fn(base_url, api_key, m.effective_upstream_id or m.id,
                       chat_path=chat_path, timeout=timeout)
        results.append(res)
        if res.verdict == INCONCLUSIVE:
            consecutive_inconclusive += 1
        else:
            consecutive_inconclusive = 0
        if delay and i < len(models) - 1:
            time.sleep(delay)

    # Guard: key expired / network down -> per-model verdicts are void.
    judgeable = [r for r in results if r.verdict not in (AUTH, INCONCLUSIVE)]
    if not judgeable:
        kinds = {r.verdict for r in results}
        print(f"  ⚠ {provider_name}: probe batch inconclusive ({', '.join(sorted(kinds))}) "
              f"-- keeping all {len(models)} models unverified")
        return list(models), results

    kept = [m for m, r in zip(models, results) if r.kept]
    for m, r in zip(models, results):
        if not r.kept:
            print(f"  ✗ {provider_name}: {r.model} dead upstream "
                  f"(HTTP {r.status}) -- dropped")
    print(f"  ✓ {provider_name}: probe-verified {len(kept)}/{len(models)} models usable")
    return kept, results
