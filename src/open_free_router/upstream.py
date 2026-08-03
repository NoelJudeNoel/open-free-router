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


class _TierRouter:
    def __init__(self) -> None:
        self.num_retries = DEFAULT_NUM_RETRIES
        self.cooldowns = _Cooldown()


_router = _TierRouter()


def reset_tier_state() -> None:
    """Clear all cooldowns — call after the registry is rebuilt."""
    _router.cooldowns = _Cooldown()


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
    """Swap model id to the upstream id and clamp max_tokens to capability."""
    patched = dict(req)
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
    """Open a single upstream request. Returns (response, conn)."""
    url = urlsplit(inst.provider.upstream_url)
    if url.scheme == "https":
        conn = http.client.HTTPSConnection(url.hostname, url.port or 443, timeout=timeout)
    else:
        conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=timeout)
    conn.connect()
    conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    conn.request("POST", path, body=data, headers=headers)
    return conn.getresponse(), conn


def _endpoint_path(req: dict[str, Any]) -> str:
    """Resolve the upstream path from req['_endpoint_path'], ensuring a
    leading slash (urlsplit('chat/completions').path has none)."""
    path = urlsplit(req.get("_endpoint_path", "/v1/chat/completions")).path
    if not path.startswith("/"):
        path = "/" + path
    return path


class TierStreamResult:
    """Outcome of a single upstream attempt in streaming mode."""
    __slots__ = ("status", "headers", "body", "conn", "switch_allowed")

    def __init__(self, status: int, headers, body: bytes | None,
                 conn, switch_allowed: bool):
        self.status = status
        self.headers = headers
        self.body = body
        self.conn = conn
        self.switch_allowed = switch_allowed


def forward_tier_buffered(tier: str, registry, req: dict[str, Any],
                          timeout: int, *, num_retries: int = DEFAULT_NUM_RETRIES,
                          request_context: int = 0
                          ) -> tuple[int, bytes, dict[str, str]]:
    """Route a non-streaming request through `tier` with ordered fallback.

    context pre-filter -> per-instance retry (num_retries) -> cooldown-skip
    -> failover -> TierExhaustedError on full failure. Caller maps the
    error to a 429 response.
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
                    return status, body, hdrs
                last_status = status
                if retry_after and _is_retryable_status(status):
                    _router.cooldowns.mark(inst.key, retry_after)
                    break
                if _is_retryable_status(status) and attempt < num_retries:
                    attempt += 1
                    continue
                _router.cooldowns.mark(inst.key, retry_after)
                break
            finally:
                conn.close()
    raise TierExhaustedError(tier, last_status)


def forward_tier_streaming(tier: str, registry, req: dict[str, Any],
                           timeout: int, *, num_retries: int = DEFAULT_NUM_RETRIES,
                           request_context: int = 0):
    """Try instances in `tier` until one begins streaming without error.

    Returns (TierStreamResult, UpstreamInstance) | None:
      - status >= 400 & switch_allowed=True : headers not flushed, retry tier
      - status in (200,) : streaming started; drain result.conn.
      - None : pool exhausted -> caller raises TierExhaustedError.
    """
    from open_free_router.tiers import tier_members
    pool = tier_members(tier, registry, request_context=request_context)
    if not pool:
        return None

    path = _endpoint_path(req)
    original_headers: dict[str, Any] = req.get("_headers", {}) or {}

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
                body = resp.read()
                retry_after = resp.getheader("Retry-After")
                hdrs = {k.lower(): v for k, v in resp.getheaders()}
                if _is_retryable_status(resp.status) and attempt < num_retries:
                    attempt += 1
                    conn.close()
                    continue
                # Retries exhausted for this instance: cooldown + try next
                # instance in the pool (don't give up the whole tier yet).
                _router.cooldowns.mark(inst.key, retry_after)
                conn.close()
                break  # -> next instance in `for inst in pool`
            client_hdrs = {k.lower(): v for k, v in resp.getheaders()}
            return TierStreamResult(resp.status, client_hdrs, None, resp, False), inst
    return None
