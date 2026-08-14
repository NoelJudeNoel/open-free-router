
"""Tests for the CLI argument parser and command dispatch."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from open_free_router.cli import main


def _write_config(tmp_path, content=""):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(content)
    return cfg_path


def test_cli_no_command_prints_help(capsys):
    with pytest.raises(SystemExit):
        main(args=[])  # will print help and exit(1)
    out = capsys.readouterr().out
    assert "open-free-router" in out


def test_cli_add_creates_provider(tmp_path):
    """open-free-router add NAME --base-url URL --api-key KEY --model M"""
    _write_config(tmp_path)
    with patch("open_free_router.cli.Config") as MockConfig:
        mock_cfg = MockConfig.return_value
        mock_cfg.registry_path = tmp_path / "registry.yaml"
        with patch("open_free_router.cli.Registry") as MockReg:
            mock_reg = MockReg.load.return_value
            mock_reg.save = MagicMock()
            main(args=["add", "my-provider",
                       "--base-url", "https://api.example.com/v1",
                       "--upstream-url", "https://api.example.com/v1",
                       "--api-key", "sk-test123",
                       "--model", "model-a",
                       "--model", "model-b"])
            # save was called
            mock_reg.save.assert_called_once()
            # add_provider was called with the right provider config
            mock_reg.add_provider.assert_called_once()
            cfg = mock_reg.add_provider.call_args[0][0]
            assert cfg.name == "my-provider"
            assert cfg.base_url == "https://api.example.com/v1"
            assert cfg.api_key == "sk-test123"
            assert len(cfg.models) == 2
            assert cfg.models[0].id == "model-a"
            assert cfg.models[1].id == "model-b"


def test_cli_refresh_dry_run_does_not_save(tmp_path):
    """open-free-router refresh --dry-run should not save the registry."""
    _write_config(tmp_path)
    with patch("open_free_router.cli.Config") as MockConfig, \
         patch("open_free_router.cli.Registry") as MockReg, \
         patch("open_free_router.cli.refresh") as mock_refresh:
        mock_cfg = MockConfig.return_value
        mock_cfg.registry_path = tmp_path / "registry.yaml"
        mock_reg = MockReg.load.return_value
        mock_refresh.return_value = {"openrouter": False}
        main(args=["refresh", "--dry-run"])
        # save should NOT be called (dry-run)
        mock_reg.save.assert_not_called()
        mock_refresh.assert_called_once()


def test_cli_refresh_with_changes_saves(tmp_path):
    """open-free-router refresh (without --dry-run) saves when there are
    changes."""
    _write_config(tmp_path)
    with patch("open_free_router.cli.Config") as MockConfig, \
         patch("open_free_router.cli.Registry") as MockReg, \
         patch("open_free_router.cli.refresh") as mock_refresh:
        mock_cfg = MockConfig.return_value
        mock_cfg.registry_path = tmp_path / "registry.yaml"
        mock_cfg.registry_git_history = False
        mock_reg = MockReg.load.return_value
        mock_refresh.return_value = {"openrouter": True}
        main(args=["refresh"])
        mock_reg.save.assert_called_once()


def test_cli_sync_diff_does_not_write(tmp_path):
    """open-free-router sync --diff should not write agent configs."""
    _write_config(tmp_path)
    with patch("open_free_router.cli.Config") as MockConfig, \
         patch("open_free_router.cli.Registry") as MockReg, \
         patch("open_free_router.sync.sync_all") as mock_sync:
        mock_cfg = MockConfig.return_value
        mock_cfg.registry_path = tmp_path / "registry.yaml"
        mock_reg_obj = MockReg.load.return_value
        mock_sync.return_value = {"omp": ["m"]}
        main(args=["sync", "--diff"])
        # sync_all should be called with do_write=False
        mock_sync.assert_called_once()
        assert mock_sync.call_args[1]["do_write"] is False


def test_cli_refresh_unknown_source_exits(tmp_path):
    """open-free-router refresh --source unknown exits with code 1."""
    _write_config(tmp_path)
    with patch("open_free_router.cli.Config") as MockConfig, \
         patch("open_free_router.cli.Registry") as MockReg, \
         patch("open_free_router.cli.refresh") as mock_refresh:
        mock_cfg = MockConfig.return_value
        mock_cfg.registry_path = tmp_path / "registry.yaml"
        MockReg.load.return_value
        mock_refresh.return_value = {}
        with pytest.raises(SystemExit) as exc_info:
            main(args=["refresh", "--source", "nonexistent"])
        assert exc_info.value.code == 1
