#!/usr/bin/env python3
"""open-free-router daemon — proxy + UI + scheduler in one process."""
from __future__ import annotations

import json
import time
import traceback
import threading
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from open_free_router.config import Config
from open_free_router.registry import Registry
from open_free_router.proxy import run_proxy, rebuild_proxy_index
from open_free_router.ui import run_ui
from open_free_router.refresh import refresh
from open_free_router.sync import write_pi_models, sync_all
from open_free_router._instance_guard import (
    acquire_instance_lock,
    release_instance_lock,
    InstanceAlreadyRunningError,
)


class Daemon:
    """Runs proxy + UI + scheduler in one process."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.reg = Registry.load(cfg.registry_path)
        self._proxy_server = None
        self._lock_file = None
        self._pidfile = None
        self._stop = threading.Event()
        # Shared mutable dict (not two separate scalar attrs) so the UI
        # thread, which gets a reference to this same object, always sees
        # the current value rather than whatever it was at thread-start
        # time. Surfaced via /api/status so "the scheduler silently died"
        # is something a user can actually notice instead of just...
        # not noticing.
        self.scheduler_status = {"last_ok": None, "last_error": None}

    def _run_cycle(self):
        """One scheduler cycle: refresh, and if anything actually changed,
        persist + resync every agent config. Split out from _scheduler()'s
        loop so tests can call a single cycle directly instead of waiting
        on the real interval timer."""
        print(f"[scheduler] refreshing free models (every {self.cfg.refresh_interval_hours}h)...")
        try:
            results = refresh(self.reg)
            changed = any(results.values())
            if changed:
                self.reg.save(self.cfg.registry_path, git_history=self.cfg.registry_git_history)
                rebuild_proxy_index()
                proxy_url = f"http://{self.cfg.proxy_host}:{self.cfg.proxy_port}/v1"
                write_pi_models(self.reg, proxy_url=proxy_url)
                sync_all(self.reg, proxy_url=proxy_url)
                print("[scheduler] registry updated")
            else:
                print("[scheduler] no changes")
            self.scheduler_status["last_ok"] = datetime.now(timezone.utc).isoformat()
            self.scheduler_status["last_error"] = None
        except Exception:
            # A refresh_sources module raising (bad response shape,
            # unexpected upstream change, etc.) must not permanently kill
            # this thread — an uncaught exception here silently ends the
            # daemon's only auto-refresh loop for the rest of the
            # process's life, with no crash and no obvious signal to the
            # user. Log loudly and keep the loop alive so the next
            # scheduled cycle still runs.
            self.scheduler_status["last_error"] = traceback.format_exc()
            print("[scheduler] cycle failed, will retry next interval:", file=sys.stderr)
            print(self.scheduler_status["last_error"], file=sys.stderr)

    def _scheduler(self):
        interval_hours = self.cfg.refresh_interval_hours
        while not self._stop.wait(interval_hours * 3600):
            self._run_cycle()

    def serve(self):
        print(f"  Proxy  : {self.cfg.proxy_host}:{self.cfg.proxy_port}")
        print(f"  UI     : http://{self.cfg.ui_host}:{self.cfg.ui_port}")
        print(f"  Refresh: every {self.cfg.refresh_interval_hours}h")
        print(f"  Timeout: {self.cfg.upstream_timeout}s")
        print()

        # Single-instance guard: refuse to start if another instance is
        # already holding the pidfile lock or listening on either port.
        # Prevents the "two serves race for the same port -> Errno 98 ->
        # systemd auto-restart loop" failure mode. The lock is released
        # in the finally below on any exit path.
        lock_file, pidfile = acquire_instance_lock(
            self.cfg.data_dir,
            self.cfg.proxy_host, self.cfg.proxy_port,
            self.cfg.ui_host, self.cfg.ui_port,
        )
        self._lock_file = lock_file
        self._pidfile = pidfile

        threads = []
        try:
            # Start proxy
            srv, _ = run_proxy(self.reg, host=self.cfg.proxy_host, port=self.cfg.proxy_port,
                               upstream_timeout=self.cfg.upstream_timeout)
            self._proxy_server = srv

            # Write Pi models on startup
            proxy_url = f"http://{self.cfg.proxy_host}:{self.cfg.proxy_port}/v1"
            write_pi_models(self.reg, proxy_url=proxy_url)
            sync_all(self.reg, proxy_url=proxy_url)

            threads = [
                threading.Thread(target=run_ui, args=(self.cfg, self.cfg.ui_port, self.reg),
                                  kwargs={"scheduler_status": self.scheduler_status}, daemon=True),
                threading.Thread(target=self._scheduler, daemon=True),
            ]

            for t in threads:
                t.start()

            while not self._stop.is_set():
                self._stop.wait(1)
        except KeyboardInterrupt:
            self._stop.set()
        finally:
            print("\nShutting down...")
            if self._proxy_server:
                self._proxy_server.shutdown()
            for t in threads:
                t.join(timeout=5)
            # Release the single-instance lock last so the next start
            # can bind the ports only after we've fully shut them down.
            release_instance_lock(self._lock_file, self._pidfile)
            print("Done.")


def main():
    cfg = Config()
    Daemon(cfg).serve()