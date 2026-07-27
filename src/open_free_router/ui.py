#!/usr/bin/env python3
"""Web dashboard for open-free-router."""
from __future__ import annotations

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from open_free_router.config import Config
from open_free_router.registry import Registry


class _UIHandler(BaseHTTPRequestHandler):
    cfg: Config | None = None
    reg: Registry | None = None
    config_path: Path | None = None

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_file("templates/index.html", "text/html")
        elif self.path == "/static/style.css":
            self._serve_file("web_static/static/css/style.css", "text/css")
        elif self.path == "/static/app.js":
            self._serve_file("web_static/static/js/app.js", "application/javascript")
        elif self.path == "/api/status":
            self._api_status()
        elif self.path == "/api/models":
            self._api_models()
        elif self.path == "/api/config":
            self._api_config_get()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/config":
            self._api_config_post()
        else:
            self.send_error(404)

    def _serve_file(self, rel: str, content_type: str):
        base = Path(__file__).parent
        fpath = base / rel
        if not fpath.exists():
            self.send_error(404)
            return
        content = fpath.read_text()
        body = content.encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _api_status(self):
        status = {
            "proxy": {
                "openrouter": f"{self.cfg.proxy_host}:{self.cfg.proxy_openrouter_port}",
                "zen": f"{self.cfg.proxy_host}:{self.cfg.proxy_zen_port}",
            },
            "providers": [],
        }
        for name, p in (self.reg.providers if self.reg else {}).items():
            status["providers"].append({
                "name": name,
                "base_url": p.base_url,
                "auto_refresh": p.auto_refresh,
                "model_count": len(p.models),
                "models": [m.id for m in p.models],
            })
        self._send_json(200, status)

    def _api_models(self):
        models = {}
        for name, p in (self.reg.providers if self.reg else {}).items():
            models[name] = [m.to_dict() for m in p.models]
        self._send_json(200, models)

    def _api_config_get(self):
        if not self.config_path or not self.config_path.exists():
            self._send_json(200, {"yaml": ""})
            return
        content = self.config_path.read_text()
        self._send_json(200, {"yaml": content})

    def _api_config_post(self):
        if not self.config_path:
            self._send_json(400, {"error": "config path not set"})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            yaml_text = data.get("yaml", "")
            # Basic sanity check
            import yaml
            parsed = yaml.safe_load(yaml_text)
            if not isinstance(parsed, dict):
                raise ValueError("config root must be a mapping")
        except Exception as e:
            self._send_json(400, {"error": str(e)})
            return
        self.config_path.write_text(yaml_text)
        self._send_json(200, {"ok": True, "saved": str(self.config_path)})

    def _send_json(self, code: int, obj: dict):
        body = json.dumps(obj, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def run_ui(cfg: Config, port: int = 9527):
    _UIHandler.cfg = cfg
    reg = Registry.load(cfg.registry_path)
    _UIHandler.reg = reg
    _UIHandler.config_path = cfg.path
    srv = HTTPServer((cfg.ui_host, port), _UIHandler)
    print(f"🌐 Dashboard: http://{cfg.ui_host}:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    srv.server_close()
