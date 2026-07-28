"""Free model proxy — single port, routes by model ID to upstream provider.

GET  /v1/models           → all free models from registry
POST /v1/chat/completions → forward to the correct upstream by model ID
"""
from __future__ import annotations

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import ClassVar
from urllib.request import Request, urlopen
from urllib.error import URLError

from open_free_router.registry import Registry


class _ProxyHandler(BaseHTTPRequestHandler):
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
                    # Prefix model ID with provider name so users can distinguish
                    # which upstream provides the model (e.g. nv/z-ai/glm-5.2)
                    idx[m.id] = name
                    prefixed = f"{prefix}/{m.id}"
                    if prefixed not in idx:
                        idx[prefixed] = name
        with cls._index_lock:
            cls._model_index = idx

    def _find_provider(self, model_id: str) -> str | None:
        with self._index_lock:
            return self._model_index.get(model_id)

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
            self._handle_list_models()
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()

        if path == "/v1/chat/completions":
            self._handle_chat_completion(body)
            return
        self._send_json(404, {"error": "not found"})

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
        self._send_json(200, {"object": "list", "data": items})

    def _handle_chat_completion(self, body: str):
        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid json"})
            return

        model_id = req.get("model", "")
        provider_name = self._find_provider(model_id)
        if not provider_name:
            self._send_json(403, {
                "error": {
                    "message": f"Model '{model_id}' not in free whitelist.",
                    "type": "proxy_error",
                }
            })
            return

        p = self.registry.get(provider_name) if self.registry else None
        if not p or not (p.upstream_url or p.base_url):
            self._send_json(502, {"error": "provider not configured"})
            return

        # Look up the actual model to get upstream ID
        upstream_model_id = model_id  # fallback
        if self.registry:
            for prov in self.registry.providers.values():
                found = False
                for m in prov.models:
                    display = f"{prov.model_prefix}/{m.id}"
                    if display == model_id or m.id == model_id:
                        upstream_model_id = m.effective_upstream_id
                        found = True
                        break
                if found:
                    break
        req["model"] = upstream_model_id
        data = json.dumps(req).encode()

        upstream = (p.upstream_url or p.base_url).rstrip("/")
        key = p.effective_key
        url = f"{upstream}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
        try:
            req_out = Request(url, data=data, headers=headers, method="POST")
            with urlopen(req_out, timeout=120) as r:
                resp = r.read()
                self.send_response(r.status)
                for k, v in r.headers.items():
                    if k.lower() in ("content-type", "content-length"):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp)
        except URLError as e:
            code = getattr(e, "code", 502)
            raw = getattr(e, "read", lambda: b"")()
            if raw:
                try:
                    self._send_json(code, json.loads(raw))
                except json.JSONDecodeError:
                    self._send_json(code, {"error": raw.decode("utf-8", errors="replace")})
            else:
                self._send_json(code, {"error": str(e.reason)})
        except Exception as e:
            self._send_json(502, {"error": str(e)})

    def log_message(self, format, *args):
        pass


def run_proxy(registry: Registry, host: str = "127.0.0.1", port: int = 8337):
    handler = type("Handler", (_ProxyHandler,), {
        "registry": registry,
    })
    handler.rebuild_index()
    srv = HTTPServer((host, port), handler)
    print(f"  Proxy  : {host}:{port} (single-port, model-ID routing)")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, handler


def rebuild_proxy_index():
    """Rebuild the model-ID → provider reverse index."""
    _ProxyHandler.rebuild_index()