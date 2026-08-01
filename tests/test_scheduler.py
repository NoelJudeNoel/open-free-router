"""Tests for Daemon._run_cycle(): the scheduler must survive an
exception anywhere in refresh/save/sync (not silently die forever), and
must only push writes to agent config files when something actually
changed.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from open_free_router.config import Config
from open_free_router.registry import Registry
from open_free_router.serve import Daemon


def _daemon(tmp_path, providers=None):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "proxy:\n  host: 127.0.0.1\n  port: 18337\n"
        "ui:\n  host: 127.0.0.1\n  port: 19057\n"
        "refresh_interval_hours: 12\n"
        "registry: registry.yaml\n"
    )
    reg_path = tmp_path / "registry.yaml"
    reg_path.write_text("providers: {}\n")
    cfg = Config(config_path=cfg_path)
    d = Daemon(cfg)
    if providers is not None:
        d.reg = Registry(providers)
    return d


class TestSchedulerResilience:
    @patch("open_free_router.serve.refresh")
    def test_exception_in_refresh_does_not_propagate(self, mock_refresh, tmp_path):
        mock_refresh.side_effect = RuntimeError("boom")
        d = _daemon(tmp_path)
        d._run_cycle()  # must not raise
        assert d.scheduler_status["last_error"] is not None
        assert "boom" in d.scheduler_status["last_error"]
        assert d.scheduler_status["last_ok"] is None

    @patch("open_free_router.serve.refresh")
    def test_recovers_on_next_successful_cycle(self, mock_refresh, tmp_path):
        d = _daemon(tmp_path)
        mock_refresh.side_effect = RuntimeError("boom")
        d._run_cycle()
        assert d.scheduler_status["last_error"] is not None

        mock_refresh.side_effect = None
        mock_refresh.return_value = {}
        d._run_cycle()
        assert d.scheduler_status["last_error"] is None
        assert d.scheduler_status["last_ok"] is not None

    @patch("open_free_router.serve.refresh")
    def test_exception_in_sync_does_not_propagate(self, mock_refresh, tmp_path):
        """The bug this guards: refresh() succeeding isn't enough --
        reg.save()/rebuild_proxy_index()/write_pi_models()/sync_all() are
        also in the try block now."""
        mock_refresh.return_value = {"fake": True}
        d = _daemon(tmp_path, providers={
            "fake": {"upstream_url": "https://example.com/v1", "models": [{"id": "m1"}]},
        })
        with patch("open_free_router.serve.rebuild_proxy_index", side_effect=RuntimeError("disk full")):
            d._run_cycle()  # must not raise
        assert d.scheduler_status["last_error"] is not None
        assert "disk full" in d.scheduler_status["last_error"]


class TestSchedulerChangeGating:
    @patch("open_free_router.serve.sync_all")
    @patch("open_free_router.serve.write_pi_models")
    @patch("open_free_router.serve.rebuild_proxy_index")
    @patch("open_free_router.serve.refresh")
    def test_no_change_skips_save_and_sync(self, mock_refresh, mock_rebuild, mock_pi, mock_sync, tmp_path):
        mock_refresh.return_value = {"fake": False, "other": False}
        d = _daemon(tmp_path)
        d._run_cycle()
        mock_rebuild.assert_not_called()
        mock_pi.assert_not_called()
        mock_sync.assert_not_called()

    @patch("open_free_router.serve.sync_all")
    @patch("open_free_router.serve.write_pi_models")
    @patch("open_free_router.serve.rebuild_proxy_index")
    @patch("open_free_router.serve.refresh")
    def test_change_triggers_save_and_sync(self, mock_refresh, mock_rebuild, mock_pi, mock_sync, tmp_path):
        mock_refresh.return_value = {"fake": True}
        d = _daemon(tmp_path)
        d._run_cycle()
        mock_rebuild.assert_called_once()
        mock_pi.assert_called_once()
        mock_sync.assert_called_once()
