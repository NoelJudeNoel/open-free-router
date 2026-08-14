"""Tests for Config class."""
from pathlib import Path
from open_free_router.config import Config


def test_config_defaults(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("")
    cfg = Config(config_path=cfg_path)
    assert cfg.proxy_host == "127.0.0.1"
    assert cfg.proxy_port == 8337
    assert cfg.ui_host == "127.0.0.1"
    assert cfg.ui_port == 9057
    assert cfg.refresh_interval_hours == 12


def test_config_custom_values(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "proxy:\n  host: 0.0.0.0\n  port: 9000\n"
        "ui:\n  host: 0.0.0.0\n  port: 9001\n"
        "refresh_interval_hours: 6\n"
    )
    cfg = Config(config_path=cfg_path)
    assert cfg.proxy_host == "0.0.0.0"
    assert cfg.proxy_port == 9000
    assert cfg.ui_host == "0.0.0.0"
    assert cfg.ui_port == 9001
    assert cfg.refresh_interval_hours == 6


def test_config_registry_relative_path(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("registry: registry.yaml\n")
    cfg = Config(config_path=cfg_path)
    assert cfg.registry_path == tmp_path / "registry.yaml"


def test_config_registry_absolute_path(tmp_path):
    reg_path = tmp_path / "custom" / "my-registry.yaml"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(f"registry: {reg_path}\n")
    cfg = Config(config_path=cfg_path)
    assert cfg.registry_path == reg_path


def test_config_refresh_interval_clamped_to_minimum_1(tmp_path):
    """refresh_interval_hours of 0 (or negative) would cause the scheduler
    to spin: threading.Event.wait(0) returns immediately, busy-looping
    and burning CPU. It must be clamped to a minimum of 1."""
    for bad in ("0", "-1", "-5"):
        cfg_path = tmp_path / f"cfg-{bad}.yaml"
        cfg_path.write_text(f"refresh_interval_hours: {bad}\n")
        cfg = Config(config_path=cfg_path)
        assert cfg.refresh_interval_hours == 1, f"{bad} did not clamp to 1"


def test_config_refresh_interval_normal_value(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("refresh_interval_hours: 24\n")
    cfg = Config(config_path=cfg_path)
    assert cfg.refresh_interval_hours == 24
