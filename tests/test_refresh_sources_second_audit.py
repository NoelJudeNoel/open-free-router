"""Tests for refresh_sources/groq.py, google_ai_studio.py, poolside.py --
previously had zero dedicated coverage (same blind spot as nvidia_nim.py
before it). Sample payloads are shaped to match each provider's real
/v1/models (or Gemini-style /models) response format, based on entries
confirmed via web research on 2026-08-02 -- see each module's own
KNOWN_FREE comment for exact confidence/sourcing per entry.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from open_free_router.refresh_sources import groq, google_ai_studio, poolside


def _mock_response(json_data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    return resp


class TestGroq:
    SAMPLE = {
        "data": [
            {"id": "openai/gpt-oss-20b", "context_window": 131072},
            {"id": "openai/gpt-oss-120b", "context_window": 131072},
            {"id": "llama-guard-3-8b", "context_window": 8192},
            # present upstream, must be excluded -- deprecated by Groq
            # 2026-06-17, shutdown 2026-08-16 per Groq's own deprecations page
            {"id": "llama-3.3-70b-versatile", "context_window": 131072},
            {"id": "llama-3.1-8b-instant", "context_window": 131072},
            # present upstream, must be excluded (removed in an earlier pass)
            {"id": "gemma2-9b-it", "context_window": 8192},
            {"id": "mixtral-8x7b-32768", "context_window": 32768},
        ]
    }

    def test_no_api_key_returns_empty(self):
        assert groq.fetch("https://api.groq.com/openai/v1", api_key=None) == []

    @patch("requests.get")
    def test_removed_models_excluded_even_if_still_upstream(self, mock_get):
        """The whole point of the removal: even if Groq's API still
        happens to list these IDs, they must not come back just because
        they're technically present in the response."""
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = groq.fetch("https://api.groq.com/openai/v1", api_key="gsk-test")
        ids = {m.id for m in models}
        assert "gemma2-9b-it" not in ids
        assert "mixtral-8x7b-32768" not in ids
        assert "llama-3.3-70b-versatile" not in ids
        assert "llama-3.1-8b-instant" not in ids

    @patch("requests.get")
    def test_current_known_free_included(self, mock_get):
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = groq.fetch("https://api.groq.com/openai/v1", api_key="gsk-test")
        ids = {m.id for m in models}
        assert ids == set(groq.KNOWN_FREE)

    @patch("requests.get")
    def test_gpt_oss_models_flagged_reasoning(self, mock_get):
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = groq.fetch("https://api.groq.com/openai/v1", api_key="gsk-test")
        by_id = {m.id: m for m in models}
        assert by_id["openai/gpt-oss-20b"].reasoning is True
        assert by_id["openai/gpt-oss-120b"].reasoning is True

    @patch("requests.get")
    def test_guard_model_flagged_non_reasoning(self, mock_get):
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = groq.fetch("https://api.groq.com/openai/v1", api_key="gsk-test")
        by_id = {m.id: m for m in models}
        assert by_id["llama-guard-3-8b"].reasoning is False


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
