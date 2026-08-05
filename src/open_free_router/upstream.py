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
from typing import Any
from urllib.parse import urlsplit

from open_free_router.tiers import UpstreamInstance

DEFAULT_NUM_RETRIES = 1
DEFAULT_COOLDOWN = 60


class TierExhaustedError(Exception):
    """Raised when every instance in a tier pool failed for one request."""

    def __init__(self, tier: str, last_status: int | None = None):
        self.tier = tier
        self.last_status = last_status
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
        try:
            return float(value)
        except ValueError:
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
                          request_context: int = 0
                          ) -> tuple[int, bytes, dict[str, str], UpstreamInstance | None]:
    """Route a non-streaming request through `tier` with ordered fallback.

    context pre-filter -> per-instance retry (num_retries) -> cooldown-skip
    -> failover -> TierExhaustedError on full failure. Caller maps the
    error to a 429 response.

    Returns (status, body, headers, inst) -- inst is the UpstreamInstance
    that actually served the request, so callers can tell the agent which
    real provider/model handled a tier/* request (observability layer 1).
    On success, `body`'s "model" field is also rewritten from the tier
    alias (e.g. "tier/high") to inst.key (e.g. "sensenova/glm-5.2") --
    OpenAI's own API does the same thing for alias models resolving to a
    concrete snapshot, so this matches what clients already expect rather
    than inventing new behavior they'd need to know to look for.
    """
    from open_free_router.tiers import tier_members
    pool = tier_members(tier, registry, request_context=request_context)
    if not pool:
        raise TierExhaustedError(tier)

    path = _endpoint_path(req)
    original_headers: dict[str, Any] = req.get("_headers", {}) or {}
    last_status: int | None = None

    for inst in pool:
        if _router.cooldowns.in_cooldown(inst.key):
            continue
        headers = _build_headers(inst, original_headers)
        attempt = 0
        while attempt <= num_retries:
            data = json.dumps(_patch_model(req, inst)).encode()
            try:
                resp, conn = _connect(inst, path, data, headers, timeout)
            except Exception:
                attempt += 1
                continue
            try:
                status = resp.status
                body = resp.read()
                retry_after = resp.getheader("Retry-After")
                hdrs = {k.lower(): v for k, v in resp.getheaders()}
                if status < 400:
                    _router.stats.record_success(inst.key)
                    body = _rewrite_model_field(body, inst.key)
                    return status, body, hdrs, inst
                last_status = status
                _router.stats.record_failure(inst.key)
                if retry_after and _is_retryable_status(status):
                    _router.cooldowns.mark(inst.key, retry_after)
                    print(f"[tier] {inst.key} -> HTTP {status}, cooling down "
                          f"(retry_after={retry_after}s); trying next instance in '{tier}'")
                    break
                if _is_retryable_status(status) and attempt < num_retries:
                    attempt += 1
                    continue
                _router.cooldowns.mark(inst.key, retry_after)
                print(f"[tier] {inst.key} -> HTTP {status}, cooling down "
                      f"(default {DEFAULT_COOLDOWN}s); trying next instance in '{tier}'")
                break
            finally:
                conn.close()
    print(f"[tier] '{tier}' exhausted -- every instance in the pool failed "
          f"(last HTTP {last_status})")
    raise TierExhaustedError(tier, last_status)


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
                           request_context: int = 0):
    """Try instances in `tier` until one begins streaming without error.

    Returns (TierStreamResult, UpstreamInstance) on success -- streaming
    started; drain result.response, then close result.raw_conn when done.

    Raises TierExhaustedError(tier, last_status) when every instance in
    the pool failed (or the pool was empty to begin with), mirroring
    forward_tier_buffered()'s contract. Previously this returned a bare
    None on exhaustion with no status info, so proxy.py's error message
    for a streaming-exhausted tier always said "last HTTP None" even
    when every instance had in fact failed with a real status code --
    inconsistent with (and strictly less useful than) the buffered
    path's error for the same failure mode.
    """
    from open_free_router.tiers import tier_members
    pool = tier_members(tier, registry, request_context=request_context)
    if not pool:
        raise TierExhaustedError(tier)

    path = _endpoint_path(req)
    original_headers: dict[str, Any] = req.get("_headers", {}) or {}
    last_status: int | None = None

    for inst in pool:
        if _router.cooldowns.in_cooldown(inst.key):
            continue
        headers = _build_headers(inst, original_headers)
        attempt = 0
        while attempt <= num_retries:
            data = json.dumps(_patch_model(req, inst)).encode()
            try:
                resp, conn = _connect(inst, path, data, headers, timeout)
            except Exception:
                attempt += 1
                continue
            if resp.status >= 400:
                last_status = resp.status
                body = resp.read()
                retry_after = resp.getheader("Retry-After")
                hdrs = {k.lower(): v for k, v in resp.getheaders()}
                _router.stats.record_failure(inst.key)
                if _is_retryable_status(resp.status) and attempt < num_retries:
                    attempt += 1
                    conn.close()
                    continue
                # Retries exhausted for this instance: cooldown + try next
                # instance in the pool (don't give up the whole tier yet).
                _router.cooldowns.mark(inst.key, retry_after)
                print(f"[tier] {inst.key} -> HTTP {resp.status}, cooling down; "
                      f"trying next instance in '{tier}' (streaming)")
                conn.close()
                break  # -> next instance in `for inst in pool`
            client_hdrs = {k.lower(): v for k, v in resp.getheaders()}
            _router.stats.record_success(inst.key)
            # Model field intentionally NOT rewritten here, unlike the
            # buffered path: doing so would mean parsing and re-serializing
            # every SSE chunk instead of the current low-overhead
            # readline()-and-relay approach (see proxy.py's streaming
            # design notes from the non-tier path this mirrors). The
            # model name upstream itself reports in each chunk (typically
            # the bare model id, e.g. "glm-5.2") is passed through as-is
            # -- not as precise as the buffered path's full
            # "provider/model" rewrite, but zero-cost and still tells the
            # agent which underlying model answered, just not which
            # provider's copy of it.
            return TierStreamResult(resp.status, client_hdrs, None, resp, conn, False), inst
    print(f"[tier] '{tier}' exhausted -- every instance in the pool failed "
          f"(last HTTP {last_status}, streaming)")
    raise TierExhaustedError(tier, last_status)
