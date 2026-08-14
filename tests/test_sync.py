"""Tests for sync functions: Pi, OpenCode, Hermes, and sync_all dispatch.

Guards the key behavior changes:
- All syncs use the PROXY_PLACEHOLDER_KEY ("open-free-router") instead of
  writing real upstream API keys into agent config files.
- Stale local-proxy providers are removed on re-sync (no duplicates).
- Hermes and OpenCode sync produce correct output format.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from open_free_router.registry import Registry
from open_free_router.sync import (
    write_pi_models, sync_opencode, sync_hermes, sync_all,
    PROXY_PLACEHOLDER_KEY,
)


def _sample_registry():
    return Registry({
        "openrouter": {
            "upstream_url": "https://openrouter.ai/api/v1",
            "api_key": "sk-or",
            "prefix": "or",
            "models": [{"id": "m1:free", "reasoning": True}],
        },
        "nvidia-nim": {
            "upstream_url": "https://api.nv.nim/v1",
            "api_key": "sk-nv-secret",
            "prefix": "nv",
            "models": [
                {"id": "glm-5.2", "context_window": 131072, "max_tokens": 4096},
            ],
        },
    })


# ── Pi ──

def test_write_pi_models_uses_placeholder_key(tmp_path):
    pi_path = tmp_path / "pi" / "models.json"
    pi_path.parent.mkdir(parents=True, exist_ok=True)
    reg = _sample_registry()
    with patch("open_free_router.sync.PI_MODELS_PATH", pi_path):
        changes = write_pi_models(reg, proxy_url="http://127.0.0.1:8337/v1")

    assert len(changes) == 2
    data = json.loads(pi_path.read_text())
    # Pi renames openrouter to \"local-free\" via _PI_PROVIDER_NAMES
    assert "local-free" in data["providers"]
    assert data["providers"]["local-free"]["apiKey"] == PROXY_PLACEHOLDER_KEY
    assert data["providers"]["nvidia-nim"]["apiKey"] == PROXY_PLACEHOLDER_KEY
    assert data["providers"]["nvidia-nim"]["baseUrl"] == "http://127.0.0.1:8337/v1"


def test_write_pi_models_skips_when_pi_dir_absent(tmp_path):
    pi_path = tmp_path / "nonexistent" / "models.json"
    reg = _sample_registry()
    with patch("open_free_router.sync.PI_MODELS_PATH", pi_path):
        changes = write_pi_models(reg, proxy_url="http://127.0.0.1:8337/v1")
    assert changes == []


def test_write_pi_models_filters_out_api_key(tmp_path):
    pi_path = tmp_path / "pi" / "models.json"
    pi_path.parent.mkdir(parents=True, exist_ok=True)
    reg = _sample_registry()
    with patch("open_free_router.sync.PI_MODELS_PATH", pi_path):
        write_pi_models(reg, proxy_url="http://127.0.0.1:8337/v1")
    raw = pi_path.read_text()
    assert "sk-or" not in raw, "real upstream key leaked into Pi config"
    assert "sk-nv-secret" not in raw, "real upstream key leaked into Pi config"


# ── OpenCode ──

def test_sync_opencode_uses_placeholder_key(tmp_path):
    oc_path = tmp_path / "opencode.jsonc"
    reg = _sample_registry()
    with patch("open_free_router.sync.OPENCODE_CONFIG", oc_path):
        changes = sync_opencode(reg, proxy_url="http://127.0.0.1:8337/v1")

    assert len(changes) == 2
    data = json.loads(oc_path.read_text())
    for name in ("openrouter", "nvidia-nim"):
        p = data["provider"][name]
        assert p["options"]["apiKey"] == PROXY_PLACEHOLDER_KEY, f"{name} apiKey is not placeholder"
        assert p["options"]["baseURL"] == "http://127.0.0.1:8337/v1"


def test_sync_opencode_removes_stale_local_providers(tmp_path):
    oc_path = tmp_path / "opencode.jsonc"
    oc_path.write_text(json.dumps({
        "provider": {
            "old-stale": {
                "name": "Old Stale",
                "options": {"baseURL": "http://127.0.0.1:8337/v1", "apiKey": "sk-old"},
            },
        },
    }))
    reg = _sample_registry()
    with patch("open_free_router.sync.OPENCODE_CONFIG", oc_path):
        changes = sync_opencode(reg, proxy_url="http://127.0.0.1:8337/v1")

    data = json.loads(oc_path.read_text())
    assert "old-stale" not in data["provider"]
    assert "openrouter" in data["provider"]


def test_sync_opencode_does_not_leak_real_key(tmp_path):
    oc_path = tmp_path / "opencode.jsonc"
    reg = _sample_registry()
    with patch("open_free_router.sync.OPENCODE_CONFIG", oc_path):
        sync_opencode(reg, proxy_url="http://127.0.0.1:8337/v1")
    raw = oc_path.read_text()
    assert "sk-or" not in raw
    assert "sk-nv-secret" not in raw


# ── Hermes ──

def test_sync_hermes_uses_placeholder_key(tmp_path):
    hermes_path = tmp_path / "config.yaml"
    hermes_path.write_text("custom_providers: []\n")
    reg = _sample_registry()
    with patch("open_free_router.sync.HERMES_CONFIG", hermes_path):
        changes = sync_hermes(reg, proxy_url="http://127.0.0.1:8337/v1")

    assert changes == ["open-free-router"]
    raw = hermes_path.read_text()
    assert PROXY_PLACEHOLDER_KEY in raw, "placeholder key not found in Hermes config"
    assert "sk-or" not in raw
    assert "sk-nv-secret" not in raw


def test_sync_hermes_noop_when_entry_already_exists(tmp_path):
    hermes_path = tmp_path / "config.yaml"
    hermes_path.write_text("custom_providers:\n  - name: open-free-router\n    base_url: http://127.0.0.1:8337/v1\n    api_key: sk-old\n")
    reg = _sample_registry()
    with patch("open_free_router.sync.HERMES_CONFIG", hermes_path):
        changes = sync_hermes(reg, proxy_url="http://127.0.0.1:8337/v1")
    assert changes == []


def test_sync_hermes_noop_when_config_missing(tmp_path):
    hermes_path = tmp_path / "nonexistent" / "config.yaml"
    reg = _sample_registry()
    with patch("open_free_router.sync.HERMES_CONFIG", hermes_path):
        changes = sync_hermes(reg, proxy_url="http://127.0.0.1:8337/v1")
    assert changes == []


# ── sync_all (dispatch) ──

def test_sync_all_dispatches_to_all_agents(tmp_path):
    pi_path = tmp_path / "pi" / "models.json"
    pi_path.parent.mkdir(parents=True)
    oc_path = tmp_path / "opencode.jsonc"
    omp_path = tmp_path / "models.yml"
    hermes_path = tmp_path / "config.yaml"
    hermes_path.write_text("custom_providers: []\n")

    reg = _sample_registry()
    with patch.multiple("open_free_router.sync",
                         PI_MODELS_PATH=pi_path,
                         OPENCODE_CONFIG=oc_path,
                         OMP_MODELS=omp_path,
                         HERMES_CONFIG=hermes_path):
        with patch("open_free_router.sync.BACKUP_DIR", tmp_path / "backup"):
            results = sync_all(reg, proxy_url="http://127.0.0.1:8337/v1")

    assert set(results.keys()) == {"pi", "omp", "opencode", "hermes"}
    for agent, changes in results.items():
        assert len(changes) > 0, f"{agent} returned no changes"


def test_sync_all_selective_agents(tmp_path):
    omp_path = tmp_path / "models.yml"
    reg = _sample_registry()
    with patch("open_free_router.sync.OMP_MODELS", omp_path):
        with patch("open_free_router.sync.BACKUP_DIR", tmp_path / "backup"):
            results = sync_all(reg, agents=["omp"], proxy_url="http://127.0.0.1:8337/v1")

    assert list(results.keys()) == ["omp"]
    assert omp_path.exists()


def test_sync_all_handles_sync_error_gracefully(tmp_path):
    reg = _sample_registry()
    def _broken_sync(*a, **kw):
        raise RuntimeError("sync failed")
    with patch("open_free_router.sync.write_pi_models", _broken_sync):
        with patch("open_free_router.sync.BACKUP_DIR", tmp_path / "backup"):
            results = sync_all(reg, agents=["pi"], do_write=False)
    assert "pi" in results
    assert "ERROR" in results["pi"][0]
