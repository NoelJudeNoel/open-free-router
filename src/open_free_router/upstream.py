"""Upstream instance driver with tiered fallback.

Encapsulates the retry / cooldown / failover policy that the proxy uses
when routing to a *tier* (tier/high, tier/mid, tier/low) instead of a
single concrete model.

Policy (mirrors LiteLLM's fallback model):
  * num_retries (default 1): re-POST to the *same* instance before bailing.
  * retryable upstream errors: 429, 5xx, timeouts, connection failures.
  * non-retryable but still cooldown-worthy: 401/403 (invalid key /
    whitelist reject) — we won't hammer a key we know is bad, but we do
    mark the instance so peers get tried first.
  * per-instance cooldown (default 60s, honoring Retry-After when present)
    so a freshly-failed instance isn't immediately retried by the next
    request in the same pool.
  * streaming: a switch is only allowed before the first byte is flushed
    to the client. Once we start relaying SSE chunks, the connection is
    locked to this upstream instance.
"""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from open_free_router.tiers import UpstreamInstance

DEFAULT_NUM_RETRIES = 1
DEFAULT_COOLDOWN = 60


@dataclass
class TierTrace:
    """Per-request failover trail, filled by forward_tier_* as it tries
    instances. The proxy reads this to inject X-OFR-* response headers
    and the opt-in `x_ofr` body field, so applications can see *what
    happened inside a tier/* request* — which instances were filtered by
    context-window, which failed (and why), which were cooled down, and
    which ultimately served — not just the final `model` field.

    This is the "open the black box" layer on top of the existing three
    observability channels (model-field rewrite, /api/status aggregate,
    [tier] log lines): those tell you *what the proxy decided*, this
    tells the *calling application* what happened *for this request*.
    """
    trace_id: str
    tier: str
    request_context: int = 0
    cascade: bool = True
    attempts: list[dict[str, Any]] = field(default_factory=list)
    filtered_keys: list[str] = field(default_factory=list)
    cascade_path: list[str] = field(default_factory=list)
    cooldowns_set: list[str] = field(default_factory=list)
    served_by: str | None = None

    def add_attempt(self, instance: str, outcome: str, *,
                    status: int | None = None, retry_after: int | None = None,
                    reason: str | None = None, ms: float = 0.0) -> None:
        self.attempts.append({
            "instance": instance,
            "outcome": outcome,
            "status": status,
            "retry_after": retry_after,
            "reason": reason,
            "ms": round(ms, 1),
        })


class TierExhaustedError(Exception):
    """Raised when every instance in a tier pool failed for one request."""

    def __init__(self, tier: str, last_status: int | None = None,
                 trace: TierTrace | None = None):
        self.tier = tier
        self.last_status = last_status
        self.trace = trace
        super().__init__(f"tier '{tier}' exhausted (last_status={last_status})")


class _Cooldown:
    """Thread-safe per-instance cooldown table."""

    def __init__(self) -> None:
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def in_cooldown(self, key: str) -> bool:
        until = self._until.get(key)
        if until is None:
            return False
        if time.monotonic() >= until:
            with self._lock:
                self._until.pop(key, None)
            return False
        return True

    def status(self, key: str) -> float | None:
        """Non-mutating peek at the cooldown-until monotonic timestamp,
        or None if not currently cooling down. Unlike in_cooldown(),
        never pops an expired entry -- for status/snapshot reporting,
        not the hot routing path, where popping expired entries keeps
        the table from growing unbounded."""
        until = self._until.get(key)
        if until is None or time.monotonic() >= until:
            return None
        return until

    def mark(self, key: str, retry_after: str | None = None) -> None:
        duration = self._parse_retry_after(retry_after) or DEFAULT_COOLDOWN
        with self._lock:
            self._until[key] = time.monotonic() + duration

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if not value:
            return None
        # Try: delta-seconds (e.g. "120")
        try:
            return float(value)
        except ValueError:
            pass
        # Try: HTTP-date (e.g. "Wed, 21 Oct 2015 07:28:00 GMT")
        import email.utils
        from datetime import datetime, timezone
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (ValueError, TypeError):
            return None
        if parsed is not None:
            now = datetime.now(timezone.utc)
            delta = (parsed - now).total_seconds()
            return max(0, delta)
        return None


class _TierStats:
    """Thread-safe per-instance success/failure counters.

    In-memory only (not persisted across restarts) -- this is meant for
    "how has this process's routing behaved recently" observability via
    /api/status, not a long-term metrics store. Reset alongside
    cooldowns in reset_tier_state(), since counts recorded against a
    provider/model identity that a registry rebuild has since changed
    or removed aren't meaningful to keep around.
    """

    def __init__(self) -> None:
        self._success: dict[str, int] = {}
        self._failure: dict[str, int] = {}
        self._lock = threading.Lock()

    def record_success(self, key: str) -> None:
        with self._lock:
            self._success[key] = self._success.get(key, 0) + 1

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._failure[key] = self._failure.get(key, 0) + 1

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            keys = set(self._success) | set(self._failure)
            return {
                k: {"success": self._success.get(k, 0), "failure": self._failure.get(k, 0)}
                for k in keys
            }


class _TierRouter:
    def __init__(self) -> None:
        self.num_retries = DEFAULT_NUM_RETRIES
        self.cooldowns = _Cooldown()
        self.stats = _TierStats()


_router = _TierRouter()


def reset_tier_state() -> None:
    """Clear all cooldowns and stats — call after the registry is rebuilt."""
    _router.cooldowns = _Cooldown()
    _router.stats = _TierStats()


def tier_status(registry) -> dict[str, list[dict[str, Any]]]:
    """Snapshot of every tier's pool, annotated with live routing state:
    success/failure counts and current cooldown status per instance.

    Layer 2 of tier-routing observability (see also: the rewritten
    `model` field on non-streaming responses, and the [tier] event log
    lines emitted on cooldown/exhaustion) -- exposed via GET
    /api/status so the dashboard (and anyone curious) can see "how has
    routing actually behaved", not just "what's configured."
    """
    from open_free_router.tiers import TIERS, tier_members
    stats = _router.stats.snapshot()
    now = time.monotonic()
    result: dict[str, list[dict[str, Any]]] = {}
    for tier_name in TIERS:
        entries = []
        for inst in tier_members(tier_name, registry):
            s = stats.get(inst.key, {"success": 0, "failure": 0})
            cooldown_until = _router.cooldowns.status(inst.key)
            entries.append({
                "instance": inst.key,
                "context_window": inst.context_window,
                "success": s["success"],
                "failure": s["failure"],
                "in_cooldown": cooldown_until is not None,
                "cooldown_seconds_remaining": (
                    max(0, round(cooldown_until - now)) if cooldown_until is not None else None
                ),
            })
        result[tier_name] = entries
    return result


def _is_retryable_status(status: int) -> bool:
    """429 (rate limited), 5xx (server errors) are retryable."""
    return status == 429 or 500 <= status < 600


def _build_headers(inst: UpstreamInstance, original: dict[str, Any]) -> dict[str, str]:
    """Build upstream headers from the provider key + passthrough headers."""
    key = inst.provider.effective_key
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "User-Agent": "open-free-router/0.1",
        "Accept": "application/json",
    }
    for k, v in original.items():
        kl = k.lower()
        if kl in ("content-type", "authorization", "content-length", "accept", "model"):
            continue
        headers[kl] = str(v if not isinstance(v, bool) else str(v).lower())
    return headers


def _patch_model(req: dict[str, Any], inst: UpstreamInstance) -> dict[str, Any]:
    """Swap model id to the upstream id and clamp max_tokens to capability.

    Also strips internal routing keys (currently "_endpoint_path" and
    "_headers", anything prefixed "_") that proxy.py stashes on `req`
    to thread state through to this module. Bug fixed here: this used
    to be a plain `dict(req)` shallow copy with no stripping, so those
    internal keys rode along into `json.dumps(patched)` and got sent
    as real fields in the chat-completion body forwarded to the actual
    third-party upstream (e.g. SenseNova, Poolside) -- an unintended
    leak of internal implementation details into every tier-routed
    request. No test caught this because every test mocks _connect()
    entirely and never inspects the serialized body that would have
    actually been sent.
    """
    patched = {k: v for k, v in req.items() if not k.startswith("_")}
    patched["model"] = inst.upstream_model
    # Strip non-standard fields that upstreams reject. Pi/OMP/OpenCode
    # agents send "include_reasoning" (reasoning-content request),
    # "reasoning" (OpenAI reasoning-effort extension), "extra_body"
    # (Python SDK meta), and "x_options" (OMP extension) -- these are
    # not in the OpenAI API spec and cause 400 from providers like
    # Google AI Studio, NVIDIA NIM, etc. The openai SDK also sends
    # "frequency_penalty", "logit_bias", and "seed" by default, which
    # Google's OpenAI-compatible endpoint rejects with 400.
    for _key in ("include_reasoning", "reasoning", "extra_body", "x_options",
                 "frequency_penalty", "logit_bias", "seed",
                 "store", "metadata", "logprobs"):
        patched.pop(_key, None)
    mm = patched.get("max_tokens")
    if mm is not None and inst.max_tokens and mm > inst.max_tokens:
        patched["max_tokens"] = inst.max_tokens
    return patched


def _request_context_len(req: dict[str, Any]) -> int:
    """Best-effort estimate of input tokens for context pre-filtering."""
    msgs = req.get("messages")
    if not isinstance(msgs, list):
        return 0
    return sum(len(str(m.get("content", ""))) // 3 for m in msgs if isinstance(m, dict))


def _connect(inst: UpstreamInstance, path: str, data: bytes,
             headers: dict[str, str], timeout: int):
    """Open a single upstream request. Returns (response, conn).

    `path` is the endpoint suffix (e.g. "/chat/completions"); the upstream
    URL's own path prefix (e.g. "/v1" for sensenova, "/v1beta" for google, or
    "/openai/v1" style prefixes some providers use) is prepended so the
    final request hits the real OpenAI-compatible route — mirroring how
    proxy.py builds ``f"{upstream_url}/{endpoint_suffix}"``.
    """
    url = urlsplit(inst.provider.upstream_url)
    # base path from upstream URL, with no trailing slash
    base = url.path.rstrip("/")
    # `path` always starts with "/" here (see _endpoint_path)
    full_path = base + path
    if url.scheme == "https":
        conn = http.client.HTTPSConnection(url.hostname, url.port or 443, timeout=timeout)
    else:
        conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=timeout)
    conn.connect()
    conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    conn.request("POST", full_path, body=data, headers=headers)
    return conn.getresponse(), conn


def _endpoint_path(req: dict[str, Any]) -> str:
    """Resolve the upstream path from req['_endpoint_path'], ensuring a
    leading slash (urlsplit('chat/completions').path has none)."""
    path = urlsplit(req.get("_endpoint_path", "/v1/chat/completions")).path
    if not path.startswith("/"):
        path = "/" + path
    return path


class TierStreamResult:
    """Outcome of a single upstream attempt in streaming mode.

    Holds both the HTTPResponse (for .readline()-based SSE relay, same
    approach as the non-tier streaming path) and the underlying
    HTTPConnection/HTTPSConnection separately, because closing the
    former does not reliably release the latter's socket. The previous
    version of this class only kept the response (confusingly in a
    field literally named `conn`) and discarded the real connection
    object returned by _connect() without ever closing it -- a slow
    socket/file-descriptor leak under sustained tier-streaming traffic,
    since Python's GC finalizing the object eventually isn't the same
    as deterministic cleanup.
    """
    __slots__ = ("status", "headers", "body", "response", "raw_conn", "switch_allowed")

    def __init__(self, status: int, headers, body: bytes | None,
                 response, raw_conn, switch_allowed: bool):
        self.status = status
        self.headers = headers
        self.body = body
        self.response = response
        self.raw_conn = raw_conn
        # Always False in the current implementation -- reserved for the
        # "switch upstream mid-stream before the first byte reaches the
        # client" behavior described in this module's top docstring, but
        # that logic was never actually built, and nothing currently
        # reads this field. Documenting this explicitly rather than
        # leaving it looking like functioning-but-unverified behavior.
        self.switch_allowed = switch_allowed


def forward_tier_buffered(tier: str, registry, req: dict[str, Any],
                          timeout: int, *, num_retries: int = DEFAULT_NUM_RETRIES,
                          request_context: int = 0, cascade: bool = True,
                          trace: TierTrace | None = None,
                          ) -> tuple[int, bytes, dict[str, str], UpstreamInstance | None]:
    """Route a non-streaming request through a tier with ordered fallback.

    context pre-filter -> per-instance retry (num_retries) -> cooldown-skip
    -> failover -> TierExhaustedError on full failure. Caller maps the
    error to a 429 response.

    Returns (status, body, headers, inst). If trace is provided, the
    full attempt trail is recorded into it (filtered/failed/cooldown/
    served instances) so the proxy can surface per-request observability
    via X-OFR-* headers / opt-in body.
    """
    from open_free_router.tiers import (
        tier_members, tier_cascade_pool, tier_filtered_instances, _TIER_CASCADE,
    )
    primary_pool = tier_members(tier, registry, request_context=request_context)
    primary_keys = {i.key for i in primary_pool}
    if cascade:
        pool = tier_cascade_pool(tier, registry, request_context=request_context)
    else:
        pool = primary_pool
    if trace:
        trace.request_context = request_context
        trace.cascade_path = list(_TIER_CASCADE.get(tier, [tier])) if cascade else [tier]
        seen_filt: set[str] = set()
        for t_name in trace.cascade_path:
            for inst in tier_filtered_instances(t_name, registry, request_context):
                if inst.key not in seen_filt:
                    seen_filt.add(inst.key)
                    trace.filtered_keys.append(inst.key)
                    trace.add_attempt(inst.key, "filtered",
                                      reason=f"context_window {inst.context_window} < {request_context}")
    if not pool:
        raise TierExhaustedError(tier, trace=trace)

    tid = trace.trace_id if trace else "-"
    path = _endpoint_path(req)
    original_headers: dict[str, Any] = req.get("_headers", {}) or {}
    last_status: int | None = None

    for inst in pool:
        if _router.cooldowns.in_cooldown(inst.key):
            if trace:
                trace.add_attempt(inst.key, "cooldown_skip",
                                  reason="instance in cooldown")
            continue
        headers = _build_headers(inst, original_headers)
        attempt = 0
        while attempt <= num_retries:
            data = json.dumps(_patch_model(req, inst)).encode()
            t0 = time.monotonic()
            try:
                resp, conn = _connect(inst, path, data, headers, timeout)
            except Exception as exc:
                ms = (time.monotonic() - t0) * 1000
                if trace:
                    trace.add_attempt(inst.key, "error",
                                      reason=f"connect: {type(exc).__name__}", ms=ms)
                attempt += 1
                continue
            try:
                status = resp.status
                body = resp.read()
                ms = (time.monotonic() - t0) * 1000
                retry_after = resp.getheader("Retry-After")
                hdrs = {k.lower(): v for k, v in resp.getheaders()}
                if status < 400:
                    _router.stats.record_success(inst.key)
                    body = _rewrite_model_field(body, inst.key)
                    if trace:
                        trace.served_by = inst.key
                        trace.add_attempt(inst.key, "ok", status=status, ms=ms)
                    if cascade and inst.key not in primary_keys:
                        print(f"[tier:{tid}] '{tier}' request served by cascade instance "
                              f"{inst.key} (requested tier exhausted / cooling down)")
                    return status, body, hdrs, inst
                last_status = status
                _router.stats.record_failure(inst.key)
                if trace:
                    trace.add_attempt(inst.key, "error", status=status,
                                      retry_after=int(retry_after) if retry_after else None,
                                      ms=ms)
                if retry_after and _is_retryable_status(status):
                    _router.cooldowns.mark(inst.key, retry_after)
                    if trace:
                        trace.cooldowns_set.append(inst.key)
                    print(f"[tier:{tid}] {inst.key} -> HTTP {status}, cooling down "
                          f"(retry_after={retry_after}s); trying next instance in '{tier}'")
                    break
                if _is_retryable_status(status) and attempt < num_retries:
                    attempt += 1
                    continue
                _router.cooldowns.mark(inst.key, retry_after)
                if trace:
                    trace.cooldowns_set.append(inst.key)
                print(f"[tier:{tid}] {inst.key} -> HTTP {status}, cooling down "
                      f"(default {DEFAULT_COOLDOWN}s); trying next instance in '{tier}'")
                break
            finally:
                conn.close()
    print(f"[tier:{tid}] '{tier}' exhausted -- every instance in the pool failed "
          f"(last HTTP {last_status})")
    raise TierExhaustedError(tier, last_status, trace=trace)


def _rewrite_model_field(body: bytes, instance_key: str) -> bytes:
    """Best-effort: set body["model"] = instance_key. Never raises -- an
    unparseable or unexpectedly-shaped body (e.g. an upstream returning
    something non-JSON despite a 2xx, or a legitimately different
    response shape this hasn't been tested against) must not break the
    actual response being forwarded to the client. Falls back to the
    original bytes unchanged on any failure."""
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            parsed["model"] = instance_key
            return json.dumps(parsed).encode()
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return body


def forward_tier_streaming(tier: str, registry, req: dict[str, Any],
                           timeout: int, *, num_retries: int = DEFAULT_NUM_RETRIES,
                           request_context: int = 0, cascade: bool = True,
                           trace: TierTrace | None = None):
    """Try instances in a tier until one begins streaming without error.

    Returns (TierStreamResult, UpstreamInstance) on success. Raises
    TierExhaustedError when every instance failed. If trace is provided,
    the attempt trail is recorded into it (same shape as the buffered
    path) so the proxy can surface X-OFR-* headers on streaming responses
    too (headers are sent before the first SSE byte).
    """
    from open_free_router.tiers import (
        tier_members, tier_cascade_pool, tier_filtered_instances, _TIER_CASCADE,
    )
    primary_pool = tier_members(tier, registry, request_context=request_context)
    primary_keys = {i.key for i in primary_pool}
    if cascade:
        pool = tier_cascade_pool(tier, registry, request_context=request_context)
    else:
        pool = primary_pool
    if trace:
        trace.request_context = request_context
        trace.cascade_path = list(_TIER_CASCADE.get(tier, [tier])) if cascade else [tier]
        seen_filt: set[str] = set()
        for t_name in trace.cascade_path:
            for inst in tier_filtered_instances(t_name, registry, request_context):
                if inst.key not in seen_filt:
                    seen_filt.add(inst.key)
                    trace.filtered_keys.append(inst.key)
                    trace.add_attempt(inst.key, "filtered",
                                      reason=f"context_window {inst.context_window} < {request_context}")
    if not pool:
        raise TierExhaustedError(tier, trace=trace)

    tid = trace.trace_id if trace else "-"
    path = _endpoint_path(req)
    original_headers: dict[str, Any] = req.get("_headers", {}) or {}
    last_status: int | None = None

    for inst in pool:
        if _router.cooldowns.in_cooldown(inst.key):
            if trace:
                trace.add_attempt(inst.key, "cooldown_skip",
                                  reason="instance in cooldown")
            continue
        headers = _build_headers(inst, original_headers)
        attempt = 0
        while attempt <= num_retries:
            data = json.dumps(_patch_model(req, inst)).encode()
            t0 = time.monotonic()
            try:
                resp, conn = _connect(inst, path, data, headers, timeout)
            except Exception as exc:
                ms = (time.monotonic() - t0) * 1000
                if trace:
                    trace.add_attempt(inst.key, "error",
                                      reason=f"connect: {type(exc).__name__}", ms=ms)
                attempt += 1
                continue
            ms = (time.monotonic() - t0) * 1000
            if resp.status >= 400:
                last_status = resp.status
                body = resp.read()
                retry_after = resp.getheader("Retry-After")
                _router.stats.record_failure(inst.key)
                if trace:
                    trace.add_attempt(inst.key, "error", status=resp.status,
                                      retry_after=int(retry_after) if retry_after else None,
                                      ms=ms)
                if _is_retryable_status(resp.status) and attempt < num_retries:
                    attempt += 1
                    conn.close()
                    continue
                _router.cooldowns.mark(inst.key, retry_after)
                if trace:
                    trace.cooldowns_set.append(inst.key)
                print(f"[tier:{tid}] {inst.key} -> HTTP {resp.status}, cooling down; "
                      f"trying next instance in '{tier}' (streaming)")
                conn.close()
                break  # -> next instance in for inst in pool
            client_hdrs = {k.lower(): v for k, v in resp.getheaders()}
            _router.stats.record_success(inst.key)
            if trace:
                trace.served_by = inst.key
                trace.add_attempt(inst.key, "ok", status=resp.status, ms=ms)
            if cascade and inst.key not in primary_keys:
                print(f"[tier:{tid}] '{tier}' stream served by cascade instance "
                      f"{inst.key} (requested tier exhausted / cooling down)")
            return TierStreamResult(resp.status, client_hdrs, None, resp, conn, False), inst
    print(f"[tier:{tid}] '{tier}' exhausted -- every instance in the pool failed "
          f"(last HTTP {last_status}, streaming)")
    raise TierExhaustedError(tier, last_status, trace=trace)