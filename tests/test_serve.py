"""Tests for sync.py (write_pi_models)."""
import json
from pathlib import Path
from unittest.mock import patch

from open_free_router.registry import Registry
from open_free_router.sync import write_pi_models


def test_write_pi_models_creates_correct_format(tmp_path):
    pi_path = tmp_path / ".pi" / "agent" / "models.json"
    pi_path.parent.mkdir(parents=True)

    reg = Registry({
        "openrouter": {
            "upstream_url": "https://openrouter.ai/api/v1",
            "prefix": "or",
            "models": [{"id": "m1:free"}, {"id": "m2:free"}],
        },
        "deepseek": {
            "upstream_url": "https://api.deepseek.com/v1",
            "prefix": "ds",
            "models": [{"id": "deepseek-chat"}],
        },
    })

    with patch("open_free_router.sync.PI_MODELS_PATH", pi_path):
        write_pi_models(reg, proxy_url="http://127.0.0.1:8337/v1")

    data = json.loads(pi_path.read_text())
    assert isinstance(data, dict)
    assert "providers" in data
    assert len(data["providers"]) == 2

    for name, p in data["providers"].items():
        assert p["baseUrl"] == "http://127.0.0.1:8337/v1"
        assert "models" in p
        for m in p["models"]:
            assert "id" in m
            assert "contextWindow" in m
            assert "maxTokens" in m
            # Pi requires an explicit api per model; all providers here
            # point at the OpenAI-compatible local proxy.
            assert m["api"] == "openai-completions"

    assert data["providers"]["local-free"]["models"][0]["id"] == "or/m1:free"
    assert data["providers"]["deepseek"]["models"][0]["id"] == "ds/deepseek-chat"


def test_write_pi_models_skips_if_no_pi_dir(tmp_path):
    pi_path = tmp_path / ".pi" / "agent" / "models.json"
    # Don't create the directory
    reg = Registry({})
    with patch("open_free_router.sync.PI_MODELS_PATH", pi_path):
        write_pi_models(reg)
    assert not pi_path.exists()
