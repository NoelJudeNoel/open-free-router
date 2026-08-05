"""Tests for refresh_sources/google_ai_studio.py, poolside.py --
previously had zero dedicated coverage (same blind spot as nvidia_nim.py
before it; groq.py also got coverage in this same original pass, but
was later removed as a provider entirely). Sample payloads are shaped
to match each provider's real /v1/models (or Gemini-style /models)
response format, based on entries confirmed via web research on
2026-08-02 -- see each module's own KNOWN_FREE comment for exact
confidence/sourcing per entry.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from open_free_router.refresh_sources import google_ai_studio, poolside


def _mock_response(json_data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    return resp


class TestGoogleAiStudio:
    SAMPLE = {
        "models": [
            {"name": "models/gemini-3.6-flash", "displayName": "Gemini 3.6 Flash", "inputTokenLimit": 1048576},
            {"name": "models/gemini-3.5-flash-lite", "displayName": "Gemini 3.5 Flash-Lite", "inputTokenLimit": 1048576},
            {"name": "models/gemma-3-27b-it", "displayName": "Gemma 3 27B", "inputTokenLimit": 131072},
            # present upstream, must be excluded (removed: left free tier 2026-04-01)
            {"name": "models/gemini-2.5-pro", "displayName": "Gemini 2.5 Pro", "inputTokenLimit": 2097152},
            # present upstream, must be excluded (removed: returning 404 in
            # production since 2026-07-09 despite a later stated shutdown date)
            {"name": "models/gemini-2.5-flash", "displayName": "Gemini 2.5 Flash", "inputTokenLimit": 1048576},
        ]
    }

    def test_no_api_key_returns_empty(self):
        assert google_ai_studio.fetch("https://generativelanguage.googleapis.com/v1beta", api_key=None) == []

    @patch("requests.get")
    def test_removed_models_excluded_even_if_still_listed_upstream(self, mock_get):
        """gemini-2.5-pro left the free tier; gemini-2.5-flash is
        reportedly already returning 404s in production ahead of its
        stated shutdown date. Either way, a stale/inconsistent API
        response listing them must not be treated as free."""
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = google_ai_studio.fetch("https://generativelanguage.googleapis.com/v1beta", api_key="AIza-test")
        ids = {m.id for m in models}
        assert "gemini-2.5-pro" not in ids
        assert "gemini-2.5-flash" not in ids

    @patch("requests.get")
    def test_current_known_free_included(self, mock_get):
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = google_ai_studio.fetch("https://generativelanguage.googleapis.com/v1beta", api_key="AIza-test")
        ids = {m.id for m in models}
        assert ids == set(google_ai_studio.KNOWN_FREE)

    @patch("requests.get")
    def test_strips_models_prefix(self, mock_get):
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = google_ai_studio.fetch("https://generativelanguage.googleapis.com/v1beta", api_key="AIza-test")
        for m in models:
            assert not m.id.startswith("models/")


class TestPoolside:
    SAMPLE = {
        "data": [
            {"id": "poolside/laguna-s-2.1", "context_length": 262144, "supported_features": ["reasoning"]},
            {"id": "poolside/laguna-xs-2.1", "context_length": 262144, "supported_features": ["reasoning"]},
            {"id": "poolside/laguna-m.1", "context_length": 262144, "supported_features": ["reasoning", "tools"]},
        ]
    }

    def test_no_api_key_returns_empty(self):
        assert poolside.fetch("https://inference.poolside.ai/v1", api_key=None) == []

    @patch("requests.get")
    def test_laguna_m1_included(self, mock_get):
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = poolside.fetch("https://inference.poolside.ai/v1", api_key="ps-test")
        ids = {m.id for m in models}
        assert "laguna-m.1" in ids  # short_id strips the "poolside/" prefix

    @patch("requests.get")
    def test_upstream_id_preserved(self, mock_get):
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = poolside.fetch("https://inference.poolside.ai/v1", api_key="ps-test")
        by_id = {m.id: m for m in models}
        assert by_id["laguna-m.1"].upstream_id == "poolside/laguna-m.1"
