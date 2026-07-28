#!/usr/bin/env python3
"""open-free-router daemon — proxy + UI + scheduler in one process."""
from __future__ import annotations

import json
import time
import threading
import signal
import sys
from pathlib import Path

from open_free_router.config import Config, _PI_PROVIDER_NAMES
from open_free_router.registry import Registry
from open_free_router.proxy import run_proxy, rebuild_proxy_index
from open_free_router.ui import run_ui
from open_free_router.refresh import refresh


PI_MODELS_PATH = Path.home() / ".pi" / "agent" / "models.json"


def write_pi_models(reg: Registry, proxy_base_url: str = "http://127.0.0.1:8337/v1"):
    """Write registry models to Pi's models.json if Pi config dir exists.

    Pi expects {providers: {name: {baseUrl, models: [...]}}}
    All providers point to the local single-port proxy; routing is by model ID.
    Model IDs are prefixed with provider name (e.g. nv/glm-5.2)
    so users can distinguish which upstream provides the model.
    """
    if not PI_MODELS_PATH.parent.exists():
        return
    try:
        providers = {}
        for name, p in reg.providers.items():
            pi_name = _PI_PROVIDER_NAMES.get(name, name)
            providers[pi_name] = {
                "baseUrl": proxy_base_url,
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
        data = {"providers": providers}
        PI_MODELS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        total = sum(len(p["models"]) for p in providers.values())
        print(f"  ✓ wrote {total} models to Pi ({PI_MODELS_PATH})")
    except Exception as e:
        print(f"  ⚠ failed to write Pi models: {e}")


class Daemon:
    """Runs proxy + UI + scheduler in one process."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.reg = Registry.load(cfg.registry_path)
        self._proxy_server = None
        self._stop = threading.Event()

    def _scheduler(self):
        interval_hours = self.cfg.refresh_interval_hours
        while not self._stop.wait(interval_hours * 3600):
            print(f"[scheduler] refreshing free models (every {interval_hours}h)...")
            results = refresh(self.reg)
            if any(results.values()):
                self.reg.save(self.cfg.registry_path)
                rebuild_proxy_index()
                print("[scheduler] registry updated")
            write_pi_models(self.reg)

    def serve(self):
        print(f"  Proxy  : {self.cfg.proxy_host}:{self.cfg.proxy_port}")
        print(f"  UI     : http://{self.cfg.ui_host}:{self.cfg.ui_port}")
        print(f"  Refresh: every {self.cfg.refresh_interval_hours}h")
        print(f"  Timeout: {self.cfg.upstream_timeout}s")
        print()

        # Start proxy
        srv, _ = run_proxy(self.reg, host=self.cfg.proxy_host, port=self.cfg.proxy_port,
                           upstream_timeout=self.cfg.upstream_timeout)
        self._proxy_server = srv

        # Write Pi models on startup
        proxy_url = f"http://{self.cfg.proxy_host}:{self.cfg.proxy_port}/v1"
        write_pi_models(self.reg, proxy_base_url=proxy_url)

        threads = [
            threading.Thread(target=run_ui, args=(self.cfg, self.cfg.ui_port, self.reg), daemon=True),
            threading.Thread(target=self._scheduler, daemon=True),
        ]

        for t in threads:
            t.start()

        try:
            while not self._stop.is_set():
                self._stop.wait(1)
        except KeyboardInterrupt:
            self._stop.set()

        print("\nShutting down...")
        if self._proxy_server:
            self._proxy_server.shutdown()
        for t in threads:
            t.join(timeout=5)
        print("Done.")


def main():
    cfg = Config()
    Daemon(cfg).serve()