"""Tests for the refresh_sources modules added to close the gap
between README's original "10 auto-refreshing providers" claim and the
6 that previously had SOURCE_MAP entries: nous, sensenova,
opencode_zen (stepfun was also added in this same original pass, but
was later removed as a provider entirely -- see git history for that
module's tests before removal).

Sample payloads below are trimmed/adapted from real (sanitized) API
responses captured manually against each provider on 2026-07-31 -- not
fabricated from documentation guesses. requests.get is mocked so these
run offline in CI; see each module's docstring for the manual
`refresh --source <name>` re-check these still need periodically since
that live verification can't run in this repo's sandboxed CI.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from open_free_router.refresh_sources import nous, sensenova, opencode_zen, openrouter


def _mock_response(json_data, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


class TestNous:
    SAMPLE = {
        "data": [
            {
                "id": "deepseek/deepseek-v4-flash-0731",
                "name": "DeepSeek: DeepSeek V4 Flash 0731",
                "context_length": 1048576,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "pricing": {"prompt": "0.0000001120", "completion": "0.0000002240"},
                "reasoning": {"default_enabled": True},
            },
            {
                "id": "some-org/free-model",
                "name": "A Free Model",
                "context_length": 131072,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "pricing": {"prompt": "0", "completion": "0"},
                "reasoning": {"default_enabled": False},
            },
            {
                "id": "some-org/free-tts",
                "name": "Free but audio-only",
                "context_length": 4096,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["audio"]},
                "pricing": {"prompt": "0", "completion": "0"},
            },
        ]
    }

    def test_no_api_key_returns_empty(self):
        assert nous.fetch("https://inference-api.nousresearch.com/v1", api_key=None) == []

    @patch("requests.get")
    def test_filters_by_zero_pricing(self, mock_get):
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = nous.fetch("https://inference-api.nousresearch.com/v1", api_key="sk-test")
        ids = [m.id for m in models]
        assert "some-org/free-model" in ids
        assert "deepseek/deepseek-v4-flash-0731" not in ids  # non-zero pricing

    @patch("requests.get")
    def test_skips_non_text_output(self, mock_get):
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = nous.fetch("https://inference-api.nousresearch.com/v1", api_key="sk-test")
        ids = [m.id for m in models]
        assert "some-org/free-tts" not in ids  # free but audio-only output

    @patch("requests.get")
    def test_fetch_failure_returns_empty(self, mock_get):
        mock_get.side_effect = ConnectionError("boom")
        assert nous.fetch("https://inference-api.nousresearch.com/v1", api_key="sk-test") == []

    @patch("requests.get")
    def test_sends_bearer_auth(self, mock_get):
        mock_get.return_value = _mock_response({"data": []})
        nous.fetch("https://inference-api.nousresearch.com/v1", api_key="sk-abc")
        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer sk-abc"


class TestSensenova:
    # Adapted from a real (sanitized) response.
    SAMPLE = {
        "data": [
            {
                "id": "sensenova-6.7-flash-lite",
                "name": "sensenova-6.7-flash-lite",
                "context_length": 262144,
                "max_output_length": 65536,
                "output_modalities": ["text"],
                "pricing": {"prompt": "0", "completion": "0"},
                "supported_features": ["tools", "json_mode", "reasoning"],
            },
            {
                "id": "some-paid-model",
                "name": "some-paid-model",
                "context_length": 32768,
                "max_output_length": 8192,
                "output_modalities": ["text"],
                "pricing": {"prompt": "0.000002", "completion": "0.000004"},
                "supported_features": [],
            },
        ]
    }

    def test_no_api_key_returns_empty(self):
        assert sensenova.fetch("https://token.sensenova.cn/v1", api_key=None) == []

    @patch("requests.get")
    def test_filters_by_zero_pricing(self, mock_get):
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = sensenova.fetch("https://token.sensenova.cn/v1", api_key="sk-test")
        ids = [m.id for m in models]
        assert ids == ["sensenova-6.7-flash-lite"]

    @patch("requests.get")
    def test_reasoning_flag_from_supported_features(self, mock_get):
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = sensenova.fetch("https://token.sensenova.cn/v1", api_key="sk-test")
        assert models[0].reasoning is True

    # 2026-11 live schema: /v1/models stopped returning `pricing` entirely
    # and now exposes businesses + modality lists only. Before this fix the
    # zero-pricing filter rejected every model (float(None) -> 1), so a
    # scheduled refresh would have reported "0 free models" forever.
    NEW_SCHEMA = {
        "data": [
            {
                "id": "glm-5.3",
                "context_length": 1048576,
                "max_output_length": 131072,
                "input_modalities": ["text"],
                "output_modalities": ["text"],
                "businesses": ["tokenplan", "metered"],
            },
            {
                "id": "sensenova-u1.5-lite",
                "context_length": 262144,
                "max_output_length": 65536,
                "input_modalities": ["text"],
                "output_modalities": ["image"],
                "businesses": ["tokenplan", "metered"],
            },
        ]
    }

    @patch("requests.get")
    def test_accepts_text_models_when_pricing_field_is_absent(self, mock_get):
        mock_get.return_value = _mock_response(self.NEW_SCHEMA)
        models = sensenova.fetch("https://token.sensenova.cn/v1", api_key="sk-test")
        assert [m.id for m in models] == ["glm-5.3"]
        assert models[0].context_window == 1048576


class TestOpencodeZen:
    # Adapted from a real (sanitized), unauthenticated response.
    SAMPLE = {
        "object": "list",
        "data": [
            {"id": "claude-sonnet-5", "object": "model", "owned_by": "opencode"},
            {"id": "big-pickle", "object": "model", "owned_by": "opencode"},
            {"id": "deepseek-v4-flash-free", "object": "model", "owned_by": "opencode"},
            {"id": "mimo-v2.5-free", "object": "model", "owned_by": "opencode"},
            {"id": "gpt-5.6-sol", "object": "model", "owned_by": "opencode"},
        ]
    }

    @patch("requests.get")
    def test_no_api_key_required(self, mock_get):
        """Unlike the other three sources, Zen's model list needs no auth."""
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = opencode_zen.fetch("https://opencode.ai/zen/v1", api_key=None)
        assert len(models) > 0
        _, kwargs = mock_get.call_args
        assert "headers" not in kwargs

    @patch("requests.get")
    def test_suffix_and_extra_free_detection(self, mock_get):
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = opencode_zen.fetch("https://opencode.ai/zen/v1", api_key=None)
        ids = {m.id for m in models}
        assert ids == {"big-pickle", "deepseek-v4-flash-free", "mimo-v2.5-free"}
        assert "claude-sonnet-5" not in ids  # paid, no -free suffix
        assert "gpt-5.6-sol" not in ids  # paid, no -free suffix

    @patch("requests.get")
    def test_fetch_failure_returns_empty(self, mock_get):
        mock_get.side_effect = ConnectionError("boom")
        assert opencode_zen.fetch("https://opencode.ai/zen/v1", api_key=None) == []

    # 2026-11 schema: Zen's catalog now carries cost/limit/capabilities.
    # Free is price==0 (not just the -free suffix), context is real, and
    # non-chat types (decision/image/embedding) must be excluded.
    RICH = {
        "object": "list",
        "data": [
            {
                "id": "beam-free", "type": "chat",
                "cost": {"text": {"input": {"price": 0}, "output": {"price": 0}}},
                "limit": {"context": 1048576, "output": 131072},
                "capabilities": {"reasoning": {"supported": True}},
                "modalities": {"output": ["text"]},
            },
            {
                "id": "jev-1.13-free", "type": "decision",
                "cost": {"text": {"input": {"price": 0}, "output": {"price": 0}}},
                "limit": {"context": 64000, "output": None},
                "capabilities": {"reasoning": {"supported": False}},
                "modalities": {"output": ["decision"]},
            },
            {
                "id": "claude-sonnet-5", "type": "chat",
                "cost": {"text": {"input": {"price": 2}, "output": {"price": 10}}},
                "limit": {"context": 1000000, "output": 128000},
                "capabilities": {"reasoning": {"supported": True}},
                "modalities": {"output": ["text"]},
            },
        ],
    }

    @patch("requests.get")
    def test_rich_schema_uses_price_and_real_limits(self, mock_get):
        mock_get.return_value = _mock_response(self.RICH)
        models = opencode_zen.fetch("https://opencode.ai/zen/v1", api_key=None)
        assert [m.id for m in models] == ["beam-free"]
        assert models[0].context_window == 1048576
        assert models[0].max_tokens == 16384  # clamped from 131072
        assert models[0].reasoning is True


class TestOpenRouterMetadata:
    SAMPLE = {
        "data": [
            {
                "id": "vendor/free-model:free",
                "name": "Free Model",
                "context_length": 1048576,
                "pricing": {"prompt": "0", "completion": "0"},
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "top_provider": {"max_completion_tokens": 131072},
                "supported_parameters": ["reasoning", "max_tokens", "tools"],
            },
        ]
    }

    @patch("requests.get")
    def test_max_tokens_from_top_provider_not_context(self, mock_get):
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = openrouter.fetch("https://openrouter.ai/api/v1", api_key="sk-test")
        assert models[0].context_window == 1048576
        assert models[0].max_tokens == 16384  # clamped, not context_length

    @patch("requests.get")
    def test_reasoning_from_supported_parameters(self, mock_get):
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = openrouter.fetch("https://openrouter.ai/api/v1", api_key="sk-test")
        assert models[0].reasoning is True


class TestNousMetadata:
    SAMPLE = {
        "data": [
            {
                "id": "org/model:free",
                "name": "Model",
                "context_length": 262144,
                "pricing": {"prompt": "0", "completion": "0"},
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "top_provider": {"max_completion_tokens": 65536},
                "supported_parameters": ["reasoning"],
            },
        ]
    }

    @patch("requests.get")
    def test_max_tokens_and_reasoning_from_openrouter_shape(self, mock_get):
        mock_get.return_value = _mock_response(self.SAMPLE)
        models = nous.fetch("https://inference-api.nousresearch.com/v1", api_key="sk-test")
        assert models[0].max_tokens == 16384
        assert models[0].reasoning is True
