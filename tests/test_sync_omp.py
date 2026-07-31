"""Tests for the ruamel.yaml-based sync_omp().

The main thing being guarded against here is the failure mode of the
previous regex-based implementation: touching/losing hand-written
provider blocks, comments, or formatting that had nothing to do with
open-free-router's own entries.
"""
from __future__ import annotations

from unittest.mock import patch

from ruamel.yaml import YAML

from open_free_router.registry import Registry
from open_free_router.sync import sync_omp


def _read_yaml(path):
    yaml = YAML()
    with open(path) as f:
        return yaml.load(f)


def _sample_registry():
    return Registry({
        "openrouter": {
            "upstream_url": "https://openrouter.ai/api/v1",
            "api_key": "sk-or",
            "prefix": "or",
            "models": [{"id": "m1:free", "reasoning": True}],
        },
    })


def test_creates_file_and_provider_block(tmp_path):
    omp_path = tmp_path / "models.yml"
    reg = _sample_registry()
    with patch("open_free_router.sync.OMP_MODELS", omp_path):
        changes = sync_omp(reg, proxy_url="http://127.0.0.1:8337/v1")

    assert changes == ["openrouter"]
    doc = _read_yaml(omp_path)
    assert doc["providers"]["openrouter"]["baseUrl"] == "http://127.0.0.1:8337/v1"
    assert doc["providers"]["openrouter"]["models"][0]["id"] == "m1:free"
    assert doc["providers"]["openrouter"]["models"][0]["reasoning"] is True


def test_preserves_unrelated_hand_configured_provider_and_comments(tmp_path):
    """A provider that does NOT point at the local proxy, plus a comment,
    must survive a sync completely untouched."""
    omp_path = tmp_path / "models.yml"
    omp_path.write_text(
        "providers:\n"
        "  # my own anthropic key, do not touch\n"
        "  anthropic-direct:\n"
        "    baseUrl: https://api.anthropic.com/v1\n"
        "    apiKey: sk-ant-real-key\n"
        "    api: anthropic\n"
        "    models:\n"
        "      - id: claude-opus-4-8\n"
        "        name: Claude Opus\n"
    )
    reg = _sample_registry()
    with patch("open_free_router.sync.OMP_MODELS", omp_path):
        sync_omp(reg, proxy_url="http://127.0.0.1:8337/v1")

    raw = omp_path.read_text()
    assert "# my own anthropic key, do not touch" in raw
    doc = _read_yaml(omp_path)
    assert doc["providers"]["anthropic-direct"]["apiKey"] == "sk-ant-real-key"
    assert doc["providers"]["anthropic-direct"]["baseUrl"] == "https://api.anthropic.com/v1"
    # and the new local-proxy provider was still added alongside it
    assert doc["providers"]["openrouter"]["baseUrl"] == "http://127.0.0.1:8337/v1"


def test_removes_stale_local_proxy_provider_no_longer_in_registry(tmp_path):
    omp_path = tmp_path / "models.yml"
    omp_path.write_text(
        "providers:\n"
        "  old-provider:\n"
        "    baseUrl: http://127.0.0.1:8337/v1\n"
        "    apiKey: sk-no-key\n"
        "    api: openai-completions\n"
        "    models: []\n"
    )
    reg = _sample_registry()  # does NOT contain "old-provider"
    with patch("open_free_router.sync.OMP_MODELS", omp_path):
        sync_omp(reg, proxy_url="http://127.0.0.1:8337/v1")

    doc = _read_yaml(omp_path)
    assert "old-provider" not in doc["providers"]
    assert "openrouter" in doc["providers"]


def test_reruns_are_idempotent_no_duplicate_blocks(tmp_path):
    omp_path = tmp_path / "models.yml"
    reg = _sample_registry()
    with patch("open_free_router.sync.OMP_MODELS", omp_path):
        sync_omp(reg, proxy_url="http://127.0.0.1:8337/v1")
        sync_omp(reg, proxy_url="http://127.0.0.1:8337/v1")

    doc = _read_yaml(omp_path)
    assert list(doc["providers"].keys()) == ["openrouter"]


def test_dry_run_does_not_write(tmp_path):
    omp_path = tmp_path / "models.yml"
    reg = _sample_registry()
    with patch("open_free_router.sync.OMP_MODELS", omp_path):
        changes = sync_omp(reg, do_write=False, proxy_url="http://127.0.0.1:8337/v1")

    assert changes == ["openrouter"]
    assert not omp_path.exists()
