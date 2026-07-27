#!/usr/bin/env python3
"""open-free-router daemon — proxy + UI + scheduler in one process."""
from __future__ import annotations

import json
import time
import threading
import signal
import sys
from pathlib import Path

from open_free_router.config import Config
from open_free_router.registry import Registry
from open_free_router.proxy import run_proxy, rebuild_proxy_index
from open_free_router.ui import run_ui
from open_free_router.refresh import refresh


PI_MODELS_PATH = Path.home() / ".pi" / "agent" / "models.json"


def write_pi_models(reg: Registry):
    """Write registry models to Pi's models.json if Pi config dir exists."""
    if not PI_MODELS_PATH.parent.exists():
        return
    try:
        models = []
        for p in reg.providers.values():
            for m in p.models:
                models.append({
                    "id": m.id,
                    "name": m.name or m.id,
                    "context_window": m.context_window,
                    "max_tokens": m.max_tokens,
                    "reasoning": m.reasoning,
                })
        PI_MODELS_PATH.write_text(json.dumps(models, indent=2, ensure_ascii=False) + "\n")
        print(f"  ✓ wrote {len(models)} models to Pi ({PI_MODELS_PATH})")
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
        interval_hours = 12
        while not self._stop.wait(interval_hours * 3600):
            print("[scheduler] refreshing free models...")
            results = refresh(self.reg)
            if any(results.values()):
                self.reg.save(self.cfg.registry_path)
                rebuild_proxy_index()
                print("[scheduler] registry updated")
            write_pi_models(self.reg)

    def serve(self):
        print(f"  Proxy  : {self.cfg.proxy_host}:{self.cfg.proxy_port}")
        print(f"  UI     : http://{self.cfg.ui_host}:{self.cfg.ui_port}")
        print("  Refresh: every 12h")
        print()

        # Start proxy
        srv, _ = run_proxy(self.reg, host=self.cfg.proxy_host, port=self.cfg.proxy_port)
        self._proxy_server = srv

        # Write Pi models on startup
        write_pi_models(self.reg)

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