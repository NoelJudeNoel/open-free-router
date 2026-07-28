#!/usr/bin/env python3
"""Web dashboard for open-free-router."""
from __future__ import annotations

import json
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from open_free_router.config import Config
from open_free_router.registry import ModelInfo, ProviderConfig, Registry
from open_free_router.proxy import rebuild_proxy_index

PI_MODELS_PATH = Path.home() / ".pi" / "agent" / "models.json"


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
        elif self.path == "/api/providers":
            self._api_providers()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/config":
            self._api_config_post()
        elif self.path == "/api/refresh":
            self._api_refresh()
        elif self.path == "/api/providers":
            self._api_providers_post()
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
            import yaml
            parsed = yaml.safe_load(yaml_text)
            if not isinstance(parsed, dict):
                raise ValueError("config root must be a mapping")
        except Exception as e:
            self._send_json(400, {"error": str(e)})
            return
        self.config_path.write_text(yaml_text)
        self._send_json(200, {"ok": True, "saved": str(self.config_path)})

    def _mask_key(self, key: str) -> str:
        if not key:
            return ""
        return f"{key[:8]}...{key[-4:]}" if len(key) > 12 else "***"

    def _api_providers(self):
        if not self.reg:
            self._send_json(200, {"providers": []})
            return
        providers = []
        for name, p in self.reg.providers.items():
            providers.append({
                "name": name,
                "base_url": p.base_url,
                "upstream_url": p.upstream_url or "",
                "api_key": self._mask_key(p.effective_key),
                "auto_refresh": p.auto_refresh,
                "refresh_method": p.refresh_method,
                "model_count": len(p.models),
                "models": [m.to_dict() for m in p.models],
            })
        self._send_json(200, {"providers": providers})

    def _api_refresh(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}
        provider_name = data.get("provider")
        from open_free_router.refresh import refresh
        results = refresh(self.reg, provider_name=provider_name)
        changed = any(v for v in results.values())
        if changed:
            assert self.reg is not None
            assert self.cfg is not None
            self.reg.save(self.cfg.registry_path)
        rebuild_proxy_index()
        self._write_pi_models()
        self._send_json(200, {"ok": True, "results": results, "saved": changed})

    def _api_providers_post(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except Exception as e:
            self._send_json(400, {"error": f"invalid json: {e}"})
            return
        if not self.reg or not self.cfg:
            self._send_json(500, {"error": "server not initialized"})
            return
        name = data.get("name", "").strip()
        if not name:
            self._send_json(400, {"error": "name is required"})
            return
        existing = self.reg.get(name)
        api_key = data.get("api_key", existing.api_key if existing else "")
        base_url = data.get("base_url", existing.base_url if existing else "")
        upstream_url = data.get("upstream_url", existing.upstream_url if existing else "")
        models_raw = data.get("models", [])
        models = []
        for m in models_raw:
            if isinstance(m, str):
                models.append(ModelInfo(id=m))
            elif isinstance(m, dict):
                models.append(ModelInfo(
                    id=m.get("id", ""),
                    name=m.get("name", m.get("id", "")),
                    context_window=int(m.get("context_window", 131072) or 131072),
                    max_tokens=int(m.get("max_tokens", 8192) or 8192),
                    reasoning=bool(m.get("reasoning", False)),
                ))
        p = ProviderConfig(
            name=name,
            base_url=base_url,
            upstream_url=upstream_url,
            api_key=api_key,
            models=models,
            auto_refresh=bool(data.get("auto_refresh", False)),
            refresh_method=data.get("refresh_method", "api" if data.get("auto_refresh") else "manual"),
        )
        self.reg.add_provider(p)
        self.reg.save(self.cfg.registry_path)
        rebuild_proxy_index()
        self._write_pi_models()
        self._send_json(200, {"ok": True, "provider": name, "models": len(models)})

    def _write_pi_models(self):
        if not PI_MODELS_PATH.parent.exists():
            return
        if not self.reg:
            return
        try:
            proxy_url = f"http://{self.cfg.proxy_host}:{self.cfg.proxy_port}/v1" if self.cfg else "http://127.0.0.1:8337/v1"
            providers = {}
            for name, p in self.reg.providers.items():
                providers[name] = {
                    "baseUrl": proxy_url,
                    "models": [
                        {
                            "id": f"{p.model_prefix}/{m.id}",
                            "name": m.name or m.id,
                            "contextWindow": m.context_window,
                            "maxTokens": m.max_tokens,
                            "reasoning": m.reasoning,
                        }
                        for m in p.models
                    ],
                }
            PI_MODELS_PATH.write_text(json.dumps({"providers": providers}, indent=2, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _send_json(self, code: int, obj: dict):
        body = json.dumps(obj, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def run_ui(cfg: Config, port: int = 9527, reg: Registry | None = None):
    _UIHandler.cfg = cfg
    _UIHandler.reg = reg or Registry.load(cfg.registry_path)
    _UIHandler.config_path = cfg.path
    srv = ThreadingHTTPServer((cfg.ui_host, port), _UIHandler)
    print(f"🌐 Dashboard: http://{cfg.ui_host}:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    srv.server_close()
