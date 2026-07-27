"""Free model proxy — local HTTP proxy that filters out non-free models.

Runs two ports:
  - OpenRouter free-only (default 8337)
  - OpenCode Zen free-only (default 8338)

Used by agents that auto-expand full model lists (Hermes, OpenCode).
"""
from __future__ import annotations

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import ClassVar
from urllib.request import Request, urlopen
from urllib.error import URLError

from open_free_router.registry import ProviderConfig, ModelInfo, Registry


class _ProxyHandler(BaseHTTPRequestHandler):
    """Per-port proxy handler. Class attrs set by run_proxy()."""

    registry: Registry | None = None
    provider_name: str = ""
    upstream_url: str = ""
    api_key: str = ""
    _meta_cache: ClassVar[dict] = {}
    _meta_lock: ClassVar[threading.Lock] = threading.Lock()
    _fetching: ClassVar[bool] = False

    def _whitelist(self) -> set[str]:
        if not self.registry:
            return set()
        p = self.registry.get(self.provider_name)
        return p.free_model_ids() if p else set()

    @classmethod
    def _bg_fetch(cls, upstream: str, key: str):
        if cls._fetching:
            return
        cls._fetching = True
        def _work():
            try:
                url = f"{upstream.rstrip('/')}/v1/models"
                req = Request(url, headers={"Authorization": f"Bearer {key}"})
                with urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
                with cls._meta_lock:
                    for m in data.get("data", []):
                        cls._meta_cache[m["id"]] = m
            except Exception:
                pass
            finally:
                cls._fetching = False
        threading.Thread(target=_work, daemon=True).start()

    def _models_json(self) -> bytes:
        wl = self._whitelist()
        upstream = self.upstream_url
        key = self.api_key

        # Trigger async metadata fetch
        if not self._meta_cache and upstream and key:
            self._bg_fetch(upstream, key)

        items = []
        with self._meta_lock:
            cache = dict(self._meta_cache)
        for mid in sorted(wl):
            meta = cache.get(mid, {})
            items.append({
                "id": mid,
                "object": "model",
                "created": meta.get("created", 0),
                "owned_by": meta.get("owned_by", "open-free-router"),
                "pricing": meta.get("pricing", {"prompt": "0", "completion": "0"}),
                "context_length": meta.get("context_length"),
                "architecture": meta.get("architecture", {}),
            })
        return json.dumps({"object": "list", "data": items}).encode()

    def _send_json(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        if path == "/v1/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(self._models_json())
            return
        # pass-through
        status, body = self._proxy(self.path)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()

        if path == "/v1/chat/completions":
            try:
                req = json.loads(body)
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid json"})
                return
            wl = self._whitelist()
            if req.get("model") not in wl:
                self._send_json(403, {
                    "error": {
                        "message": f"Model '{req.get('model')}' not in free whitelist.",
                        "type": "proxy_error",
                    }
                })
                return
            status, resp = self._proxy(self.path, body)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(resp)
            return

        status, resp = self._proxy(self.path, body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(resp)

    def _proxy(self, path: str, body: str | None = None) -> tuple[int, bytes]:
        upstream = self.upstream_url
        key = self.api_key
        clean = path.lstrip("/")
        if clean.startswith("v1/"):
            clean = clean[3:]
        url = f"{upstream.rstrip('/')}/{clean}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
        data = body.encode() if body else None
        req = Request(url, data=data, headers=headers, method="POST" if body else "GET")
        try:
            with urlopen(req, timeout=120) as r:
                return r.status, r.read()
        except URLError as e:
            code = getattr(e, "code", 502)
            payload = getattr(e, "read", lambda: b"")() or json.dumps({"error": str(e.reason)}).encode()
            return code, payload
        except Exception as e:
            return 502, json.dumps({"error": str(e)}).encode()

    def log_message(self, format, *args):
        pass  # silence


def run_proxy(registry: Registry, host: str = "127.0.0.1", openrouter_port: int = 8337, zen_port: int = 8338):
    """Start proxy servers for all providers that need filtering."""
    servers = []

    mapping = {
        "openrouter": openrouter_port,
        "opencode-zen-free": zen_port,
    }

    for pname, port in mapping.items():
        p = registry.get(pname)
        if not p or not p.base_url:
            continue

        handler = type("Handler", (_ProxyHandler,), {
            "registry": registry,
            "provider_name": pname,
            "upstream_url": p.upstream_url or p.base_url,
            "api_key": p.effective_key,
        })
        srv = HTTPServer((host, port), handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        servers.append((pname, port, srv))
        print(f"  Proxy {pname}: {host}:{port} → {p.upstream_url or p.base_url}")

    return servers
