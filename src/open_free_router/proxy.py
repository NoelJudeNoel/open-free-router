"""Free model proxy — single port, routes by model ID to upstream provider.

GET  /v1/models           → all free models from registry
POST /v1/chat/completions → forward to the correct upstream by model ID
POST /v1/completions      → same routing, legacy completions endpoint
POST /v1/embeddings       → same routing, embeddings endpoint
"""
from __future__ import annotations

import http.client
import json
import socket
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import URLError

from open_free_router.registry import Registry


class _ProxyHandler(BaseHTTPRequestHandler):
    # Nagle's algorithm + delayed ACK otherwise coalesces the small
    # writes _forward_streaming does per SSE chunk, adding tens of ms of
    # jitter per hop and defeating the point of streaming at all.
    disable_nagle_algorithm = True

    registry: Registry | None = None
    _model_index: ClassVar[dict[str, str]] = {}  # model_id → provider_name
    _index_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def rebuild_index(cls):
        idx = {}
        if cls.registry:
            for name, p in cls.registry.providers.items():
                prefix = p.model_prefix
                for m in p.models:
                    # Register all forms of the model ID so agents can use
                    # whichever format they prefer:
                    #   1. bare id       (e.g. glm-5.2)
                    #   2. prefix/id     (e.g. nv/glm-5.2)
                    #   3. upstream_id   (e.g. z-ai/glm-5.2) — matches what
                    #      Hermes and other agents send when they show the
                    #      "provider/model" label to users
                    #   4. provider/upstream_id (e.g. openrouter/gpt-oss-20b:free)
                    #      OMP and other agents use this format
                    idx[m.id] = name
                    prefixed = f"{prefix}/{m.id}"
                    if prefixed not in idx:
                        idx[prefixed] = name
                    uid = m.effective_upstream_id
                    if uid != m.id:
                        idx[uid] = name
                        provider_prefixed = f"{name}/{uid}"
                        if provider_prefixed not in idx:
                            idx[provider_prefixed] = name
        with cls._index_lock:
            cls._model_index = idx

    def _find_provider(self, model_id: str) -> str | None:
        with self._index_lock:
            return self._model_index.get(model_id)

    def _send_json(self, code: int, obj: dict, retry_after: str | None = None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if retry_after:
            self.send_header("Retry-After", retry_after)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        if path == "/":
            self._send_json(200, {
                "service": "open-free-router",
                "version": "0.1",
                "endpoints": {
                    "models": "/v1/models",
                    "chat": "/v1/chat/completions",
                    "completions": "/v1/completions",
                    "embeddings": "/v1/embeddings",
                    "ui": f"http://{self.server.server_address[0]}:9057",
                },
                "docs": "https://github.com/NoelJudeNoel/open-free-router",
            })
            return
        if path == "/v1/models":
            self._handle_list_models()
            return
        self._send_json(404, {"error": "not found"})

    # Generous but bounded — protects the proxy process from a client (or a
    # buggy/malicious one, if this ever ends up reachable beyond localhost)
    # sending an unbounded body. 25MB comfortably covers long-context chat
    # payloads; nothing in this proxy's supported endpoints needs more.
    MAX_BODY_BYTES: ClassVar[int] = 25 * 1024 * 1024

    def do_POST(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path

        endpoint_map = {
            "/v1/chat/completions": "chat/completions",
            "/v1/completions": "completions",
            "/v1/embeddings": "embeddings",
        }
        upstream_suffix = endpoint_map.get(path)
        if not upstream_suffix:
            # Drain and discard so the connection can be reused/closed
            # cleanly, but don't bother enforcing the size limit on a
            # request we're rejecting anyway.
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                self.rfile.read(min(length, self.MAX_BODY_BYTES))
            self._send_json(404, {"error": "not found"})
            return

        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header) if length_header is not None else None
        except ValueError:
            length = None
        if length is None:
            self._send_json(411, {"error": "Content-Length required"})
            return
        if length > self.MAX_BODY_BYTES:
            self._send_json(413, {"error": f"request body too large (> {self.MAX_BODY_BYTES} bytes)"})
            self.close_connection = True
            return

        body = self.rfile.read(length).decode("utf-8", errors="replace")
        self._forward_request(upstream_suffix, body)

    def _handle_list_models(self):
        if not self.registry:
            self._send_json(200, {"object": "list", "data": []})
            return
        items = []
        for name, p in self.registry.providers.items():
            for m in p.models:
                # Show provider-prefixed ID so users can distinguish upstreams
                items.append({
                    "id": f"{p.model_prefix}/{m.id}",
                    "object": "model",
                    "created": 0,
                    "owned_by": name,
                })
        # Expose tier entry points (virtual models) so agents can request a
        # whole tier instead of a concrete model; the proxy then picks the
        # best available instance and fails over across the tier pool.
        for tid in ("tier/high", "tier/mid", "tier/low"):
            items.append({
                "id": tid,
                "object": "model",
                "created": 0,
                "owned_by": "open-free-router",
            })
        self._send_json(200, {"object": "list", "data": items})

    def _forward_request(self, endpoint_suffix: str, body: str):
        """Route + forward a POST to /v1/chat/completions, /v1/completions,
        or /v1/embeddings — model lookup and upstream routing are the same
        for all three; only the upstream path suffix and (for chat/
        completions) streaming support differ."""
        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid json"})
            return

        model_id = req.get("model", "")
        provider_name = self._find_provider(model_id)
        if not provider_name:
            # ── Tier routing (tier/high, tier/mid, tier/low) ────────────
            from open_free_router.tiers import is_tier_id
            from open_free_router.upstream import (
                forward_tier_buffered, forward_tier_streaming,
                TierExhaustedError, _request_context_len,
            )

            def _tier_exhausted(tier, last_status=None):
                self._send_json(429, {"error": {
                    "message": (f"tier '{tier}' exhausted — all free upstream "
                                f"instances failed (last HTTP {last_status})."),
                    "type": "proxy_error",
                    "tier": tier,
                }})
                return True  # signal: request handled

            if is_tier_id(model_id):
                tier = model_id.split("/", 1)[1]
                timeout = getattr(self, "_upstream_timeout", 120)
                ctx_len = _request_context_len(req)
                req["_endpoint_path"] = endpoint_suffix
                is_stream = endpoint_suffix != "embeddings" and bool(req.get("stream"))

                if is_stream:
                    try:
                        res, inst = forward_tier_streaming(
                            tier, self.registry, req, timeout, request_context=ctx_len)
                    except TierExhaustedError as e:
                        _tier_exhausted(tier, e.last_status)
                        return
                    # forward_tier_streaming() only returns via its success
                    # path (status < 400) or raises TierExhaustedError --
                    # it can never return a >=400 status, so there is no
                    # error-status branch to handle here.
                    self.send_response(res.status)
                    self.send_header("Content-Type",
                                     res.headers.get("content-type", "text/event-stream"))
                    self.send_header("Cache-Control", "no-cache" if is_stream else "no-store")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    while True:
                        line = res.response.readline()
                        if not line:
                            break
                        self.wfile.write(f"{len(line):x}\r\n".encode("ascii"))
                        self.wfile.write(line)
                        self.wfile.write(b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                    # Close the actual connection, not just the response --
                    # response.close() alone doesn't reliably release the
                    # underlying socket. See TierStreamResult's docstring.
                    res.raw_conn.close()
                    return
                # buffered tier path
                try:
                    status, body, hdrs, inst = forward_tier_buffered(
                        tier, self.registry, req, timeout, request_context=ctx_len)
                    self.send_response(status)
                    self.send_header("Content-Type",
                                     hdrs.get("content-type", "application/json"))
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                except TierExhaustedError as e:
                    _tier_exhausted(tier, e.last_status)
                    return
            self._send_json(403, {"error": {
                "message": f"Model '{model_id}' not in free whitelist.",
                "type": "proxy_error",
            }})
            return

        p = self.registry.get(provider_name) if self.registry else None
        if not p or not (p.upstream_url or p.base_url):
            self._send_json(502, {"error": "provider not configured"})
            return

        # Look up the actual model to get upstream ID
        # Only search within the matched provider p — NOT all providers,
        # because the same bare model ID (e.g. step-3.7-flash) may exist
        # in multiple providers with different upstream_ids.
        upstream_model_id = model_id  # fallback
        if p:
            for m in p.models:
                # Check all forms:
                #   prefix/id       (e.g. nv/glm-5.2)
                #   bare id         (e.g. glm-5.2)
                #   upstream_id     (e.g. z-ai/glm-5.2)
                #   provider/upstream_id (e.g. nvidia-nim/z-ai/glm-5.2)  ← OMP format
                display = f"{p.model_prefix}/{m.id}"
                prov_upstream = f"{p.name}/{m.effective_upstream_id}"
                if (display == model_id or m.id == model_id
                        or m.effective_upstream_id == model_id
                        or prov_upstream == model_id):
                    upstream_model_id = m.effective_upstream_id
                    break
        req["model"] = upstream_model_id
        # Normalize OpenAI "developer" role to "system" for upstreams that
        # only accept system/user/assistant (older OpenAI-compatible APIs).
        for msg in req.get("messages", []):
            if msg.get("role") == "developer":
                msg["role"] = "system"
        is_stream = endpoint_suffix != "embeddings" and bool(req.get("stream"))
        data = json.dumps(req).encode()

        upstream = (p.upstream_url or p.base_url).rstrip("/")
        key = p.effective_key
        url = f"{upstream}/{endpoint_suffix}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": "open-free-router/0.1",
        }
        timeout = getattr(self, "_upstream_timeout", 120)

        if is_stream:
            self._forward_streaming(url, data, headers, timeout)
            return

        self._forward_buffered(url, data, headers, timeout)

    def _forward_buffered(self, url: str, data: bytes, headers: dict, timeout: int):
        try:
            req_out = Request(url, data=data, headers=headers, method="POST")
            with urlopen(req_out, timeout=timeout) as r:
                resp = r.read()
                self.send_response(r.status)
                for k, v in r.headers.items():
                    if k.lower() in ("content-type", "content-length", "retry-after"):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp)
        except URLError as e:
            code = getattr(e, "code", 502)
            raw = getattr(e, "read", lambda: b"")()
            retry_after = e.headers.get("Retry-After") if getattr(e, "headers", None) else None
            if raw:
                try:
                    self._send_json(code, json.loads(raw), retry_after=retry_after)
                except json.JSONDecodeError:
                    self._send_json(code, {"error": raw.decode("utf-8", errors="replace")}, retry_after=retry_after)
            else:
                self._send_json(code, {"error": str(e.reason)}, retry_after=retry_after)
        except Exception as e:
            self._send_json(502, {"error": str(e)})

    def _forward_streaming(self, url: str, data: bytes, headers: dict, timeout: int):
        """Forward a `stream: true` chat completion, relaying upstream SSE
        chunks to the client as they arrive instead of buffering the whole
        response (which is what plain urlopen + one wfile.write() would do).

        If the upstream call fails or returns an error status *before* any
        body has been sent to the client, we still reply with a normal
        buffered JSON error, matching the non-streaming path. Once we've
        started relaying chunks, headers are already flushed, so a later
        upstream failure just ends the stream (mirrors a dropped connection
        mid-stream rather than a JSON error body).
        """
        parts = urlsplit(url)
        conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(parts.hostname, parts.port, timeout=timeout)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        started = False
        try:
            conn.connect()
            conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.request("POST", path, body=data, headers=headers)
            resp = conn.getresponse()

            if resp.status >= 400:
                body = resp.read()
                retry_after = resp.getheader("Retry-After")
                try:
                    self._send_json(resp.status, json.loads(body), retry_after=retry_after)
                except json.JSONDecodeError:
                    self._send_json(resp.status, {"error": body.decode("utf-8", errors="replace")}, retry_after=retry_after)
                return

            self.send_response(resp.status)
            self.send_header("Content-Type", resp.getheader("Content-Type", "text/event-stream"))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            started = True

            # NOTE: deliberately resp.readline(), not resp.read(N). SSE is
            # line-oriented, and http.client's chunked-aware read(N) keeps
            # pulling *subsequent* upstream HTTP chunks from the socket
            # (blocking on each) until it has N bytes buffered — so
            # read(4096) on a stream of many small SSE events silently
            # blocks until the entire response has arrived, defeating
            # streaming. readline() returns as soon as one line is
            # available, which is exactly the granularity SSE needs and
            # keeps latency-to-first-byte low without going byte-at-a-time.
            while True:
                line = resp.readline()
                if not line:
                    break
                self.wfile.write(f"{len(line):x}\r\n".encode("ascii"))
                self.wfile.write(line)
                self.wfile.write(b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        except Exception as e:
            if not started:
                self._send_json(502, {"error": str(e)})
            # else: client already received a 200 + partial stream; stop
            # writing and let the connection close, like an upstream drop.
        finally:
            conn.close()

    def log_message(self, format, *args):
        pass


def run_proxy(registry: Registry, host: str = "127.0.0.1", port: int = 8337, upstream_timeout: int = 120):
    handler = type("Handler", (_ProxyHandler,), {
        "registry": registry,
        "_upstream_timeout": upstream_timeout,
    })
    handler.rebuild_index()
    srv = ThreadingHTTPServer((host, port), handler)
    print(f"  Proxy  : {host}:{port} (single-port, model-ID routing)")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, handler


def rebuild_proxy_index():
    """Rebuild the model-ID → provider reverse index, and clear tier
    routing's cooldown state.

    reset_tier_state() existed (upstream.py) with a docstring saying to
    call it here, but nothing actually did -- it was only ever invoked
    from tests resetting state between test cases. Wiring it in here
    means every real call site that already calls rebuild_proxy_index()
    after a registry change (serve.py's scheduler, ui.py's provider
    add/refresh handlers, cli.py) gets this for free, rather than
    needing each one to separately remember to call it. Without this,
    a cooldown keyed on a provider+upstream_id combination could
    outlive the registry change that invalidated it -- low severity
    (worst case is a stale skip, not incorrect routing), but there's no
    reason to leave it unwired now that a fix is this cheap.
    """
    from open_free_router.upstream import reset_tier_state
    _ProxyHandler.rebuild_index()
    reset_tier_state()