#!/usr/bin/env python3
"""open-free-router daemon — proxy + UI + scheduler in one process."""
from __future__ import annotations

import os
import signal
import sys
import threading
import time
import traceback
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
        self._proxy_handler = None  # handler class created by run_proxy()
        self._lock_file = None
        self._pidfile = None
        self._stop = threading.Event()
        # Set by the SIGUSR1 handler / registry watchdog when the registry
        # file changed on disk; the main serve() loop performs the reload.
        self._reload_requested = threading.Event()
        # mtime of registry.yaml at last (re)load — used by the watchdog.
        self._registry_mtime = None
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

    def _reload_registry(self):
        """Reload registry.yaml from disk into the running daemon so a
        CLI `refresh`/`add`/`sync` (or a manual edit of registry.yaml)
        takes effect without restarting serve.

        Updates every live reference: Daemon.reg (used by the scheduler),
        the proxy handler's registry + model index, and the UI handler's
        reg. Tier cooldown state is reset via rebuild_proxy_index().
        Agent configs are NOT rewritten here — that stays the job of
        `open-free-router sync` / the scheduler cycle, so a CLI refresh
        that only wants to update the model list doesn't unexpectedly
        overwrite Pi/OMP/OpenCode/Hermes files.
        """
        try:
            new_reg = Registry.load(self.cfg.registry_path)
        except Exception as e:
            print(f"[reload] registry reload FAILED (keeping current): {e}", file=sys.stderr)
            return False
        self.reg = new_reg
        if self._proxy_handler is not None:
            self._proxy_handler.registry = new_reg
        from open_free_router.proxy import _ACTIVE_HANDLER
        if _ACTIVE_HANDLER is not None:
            _ACTIVE_HANDLER.registry = new_reg
        rebuild_proxy_index()
        # Point the UI handler at the fresh registry too.
        import open_free_router.ui as ui_mod
        ui_mod._UIHandler.reg = new_reg
        self._registry_mtime = self._registry_file_mtime()
        n = len(new_reg.providers)
        print(f"[reload] registry reloaded from {self.cfg.registry_path} ({n} providers)")
        return True

    def _registry_file_mtime(self):
        try:
            return self.cfg.registry_path.stat().st_mtime_ns
        except OSError:
            return None

    def _registry_watchdog(self, interval_seconds: float = 10.0):
        """Poll registry.yaml's mtime; reload when it changes. Catches
        CLI refresh/add/sync writes and manual file edits without needing
        the signal channel (which only fires for CLI-initiated reloads)."""
        while not self._stop.wait(interval_seconds):
            mtime = self._registry_file_mtime()
            if mtime is not None and self._registry_mtime is not None and mtime != self._registry_mtime:
                print("[reload] registry.yaml changed on disk — reloading")
                self._reload_registry()

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
            srv, handler = run_proxy(self.reg, host=self.cfg.proxy_host, port=self.cfg.proxy_port,
                                     upstream_timeout=self.cfg.upstream_timeout,
                                     tier_cascade=self.cfg.tier_cascade)
            self._proxy_server = srv
            self._proxy_handler = handler
            self._registry_mtime = self._registry_file_mtime()

            # Write Pi models on startup
            proxy_url = f"http://{self.cfg.proxy_host}:{self.cfg.proxy_port}/v1"
            write_pi_models(self.reg, proxy_url=proxy_url)
            sync_all(self.reg, proxy_url=proxy_url)

            # SIGUSR1 = "registry changed on disk, reload". Lets the CLI
            # (open-free-router refresh/add/sync) trigger an immediate
            # in-process reload after saving registry.yaml, instead of the
            # user having to restart serve. The handler only flags the
            # request; the main loop below does the actual (I/O) reload.
            def _on_sigusr1(signum, frame):
                self._reload_requested.set()

            if hasattr(signal, "SIGUSR1"):
                signal.signal(signal.SIGUSR1, _on_sigusr1)

            threads = [
                threading.Thread(target=run_ui, args=(self.cfg, self.cfg.ui_port, self.reg),
                                  kwargs={"scheduler_status": self.scheduler_status}, daemon=True),
                threading.Thread(target=self._scheduler, daemon=True),
                threading.Thread(target=self._registry_watchdog, daemon=True),
            ]

            for t in threads:
                t.start()

            while not self._stop.is_set():
                if self._reload_requested.is_set():
                    self._reload_requested.clear()
                    print("[reload] SIGUSR1 received — reloading registry")
                    self._reload_registry()
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