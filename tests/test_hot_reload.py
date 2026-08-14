"""Tests for the daemon hot-reload feature.

- Daemon._reload_registry() reloads registry.yaml from disk and repoints
  every live reference (Daemon.reg, proxy handler registry+index, UI
  handler reg) without restarting the process.
- Daemon._registry_watchdog() reloads when the file's mtime changes.
- cli._notify_daemon_reload() sends SIGUSR1 to the daemon PID from the
  pidfile after a CLI refresh/add saves registry.yaml.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from open_free_router.config import Config
from open_free_router.registry import Registry
from open_free_router.serve import Daemon


def _write_registry(path: Path, provider_name: str = "sensenova", model_id: str = "glm-5.2"):
    path.write_text(json.dumps({
        provider_name: {
            "upstream_url": "https://api.sensenova.cn/v1",
            "api_key": "sk-test",
            "prefix": "sen",
            "models": [{"id": model_id}],
        }
    }))


def _make_daemon(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("data_dir: '" + str(tmp_path / "data") + "'\nproxy:\n  host: 127.0.0.1\n  port: 8337\nui:\n  host: 127.0.0.1\n  port: 9057\n")
    cfg = Config(config_path=cfg_path)
    reg_path = cfg.registry_path
    _write_registry(reg_path)
    return Daemon(cfg), cfg, reg_path


class TestReloadRegistry:
    def test_reload_updates_daemon_reg(self, tmp_path):
        daemon, cfg, reg_path = _make_daemon(tmp_path)
        assert len(daemon.reg.providers) == 1
        # Simulate a CLI refresh adding a new provider
        _write_registry(reg_path, provider_name="openrouter", model_id="m1:free")
        ok = daemon._reload_registry()
        assert ok is True
        assert "openrouter" in daemon.reg.providers
        assert "sensenova" not in daemon.reg.providers

    def test_reload_repoints_proxy_handler(self, tmp_path):
        daemon, cfg, reg_path = _make_daemon(tmp_path)
        # Simulate the handler class created by run_proxy()
        handler_cls = type("FakeHandler", (object,), {"registry": daemon.reg})
        daemon._proxy_handler = handler_cls
        with patch("open_free_router.proxy._ACTIVE_HANDLER", handler_cls), \
             patch("open_free_router.serve.rebuild_proxy_index") as mock_rebuild:
            _write_registry(reg_path, provider_name="openrouter", model_id="m1:free")
            daemon._reload_registry()
            assert handler_cls.registry is daemon.reg
            assert "openrouter" in handler_cls.registry.providers
            mock_rebuild.assert_called_once()

    def test_reload_repoints_ui_handler(self, tmp_path):
        daemon, cfg, reg_path = _make_daemon(tmp_path)
        import open_free_router.ui as ui_mod
        old_reg = ui_mod._UIHandler.reg
        try:
            _write_registry(reg_path, provider_name="openrouter", model_id="m1:free")
            daemon._reload_registry()
            assert ui_mod._UIHandler.reg is daemon.reg
            assert "openrouter" in ui_mod._UIHandler.reg.providers
        finally:
            ui_mod._UIHandler.reg = old_reg

    def test_reload_failure_keeps_current_registry(self, tmp_path):
        daemon, cfg, reg_path = _make_daemon(tmp_path)
        old_providers = dict(daemon.reg.providers)
        # Corrupt the file
        reg_path.write_text("[[not: valid: yaml: [}}")
        ok = daemon._reload_registry()
        assert ok is False
        assert dict(daemon.reg.providers) == old_providers

    def test_watchdog_reloads_on_mtime_change(self, tmp_path):
        import os
        import threading
        import time
        daemon, cfg, reg_path = _make_daemon(tmp_path)
        time.sleep(0.01)  # ensure the initial mtime is in the past
        daemon._registry_mtime = daemon._registry_file_mtime()
        _write_registry(reg_path, provider_name="openrouter", model_id="m1:free")
        # Run the watchdog in a thread and stop it after a couple of polls
        stop_after = threading.Timer(1.0, daemon._stop.set)
        stop_after.start()
        try:
            daemon._registry_watchdog(interval_seconds=0.01)
        finally:
            stop_after.cancel()
        assert "openrouter" in daemon.reg.providers

    def test_sigusr1_handler_sets_event(self, tmp_path):
        daemon, cfg, reg_path = _make_daemon(tmp_path)
        # Simulate the signal handler wired in serve()
        def _handler(signum, frame):
            daemon._reload_requested.set()
        daemon._reload_requested.clear()
        _handler(10, None)
        assert daemon._reload_requested.is_set()


class TestCliNotifyDaemonReload:
    def test_notify_reads_pid_and_sends_sigusr1(self, tmp_path):
        from open_free_router import cli
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("data_dir: '" + str(tmp_path / "data") + "'\nproxy:\n  host: 127.0.0.1\n  port: 8337\n")
        cfg = Config(config_path=cfg_path)
        pidfile = cfg.data_dir / "open-free-router.pid"
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text("pid=4242\nproxy=127.0.0.1:8337\n")
        with patch("open_free_router.cli.os.kill") as mock_kill:
            cli._notify_daemon_reload(cfg)
            mock_kill.assert_called_once_with(4242, 10)  # SIGUSR1 = 10 on Linux

    def test_notify_silent_when_no_pidfile(self, tmp_path):
        from open_free_router import cli
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("data_dir: '" + str(tmp_path / "data") + "'\nproxy:\n  host: 127.0.0.1\n  port: 8337\n")
        cfg = Config(config_path=cfg_path)
        with patch("open_free_router.cli.os.kill") as mock_kill:
            cli._notify_daemon_reload(cfg)  # no pidfile → no-op
            mock_kill.assert_not_called()

    def test_notify_silent_when_daemon_dead(self, tmp_path):
        from open_free_router import cli
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("data_dir: '" + str(tmp_path / "data") + "'\nproxy:\n  host: 127.0.0.1\n  port: 8337\n")
        cfg = Config(config_path=cfg_path)
        pidfile = cfg.data_dir / "open-free-router.pid"
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text("pid=999999\n")  # nonexistent PID
        with patch("open_free_router.cli.os.kill", side_effect=OSError("no such process")):
            cli._notify_daemon_reload(cfg)  # must not raise
