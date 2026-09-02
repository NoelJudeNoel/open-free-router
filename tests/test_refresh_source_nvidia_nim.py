"""Tests for refresh_sources/nvidia_nim.py.

Funnel as of 2026-09-02: the whole NIM catalog is free, so detection is
"live catalog minus non-chat endpoints"; usability is NOT this module's
job anymore -- refresh() runs every candidate through probe.verify_models
(one real chat completion each) before anything reaches the registry.
That division matters for reading these tests: fetch() returning a model
is a claim that it's listed AND chat-shaped, not that it currently
answers. The 2026-09-02 audit found NIM's catalog lists ~38 chat models
that 404 on real requests (kimi-k2.6 included, while it sat in our
production registry as "healthy") -- exactly what the probe step exists
to catch.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from open_free_router.refresh_sources import nvidia_nim


def _mock_response(json_data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    return resp


SAMPLE = {
    "data": [
        # chat-capable catalog entries -> included
        {"id": "deepseek-ai/deepseek-v4-flash-0731", "object": "model",
         "context_length": 1048576},
        {"id": "deepseek-ai/deepseek-v4-pro-0813", "object": "model"},
        {"id": "minimaxai/minimax-m3", "object": "model"},
        {"id": "moonshotai/kimi-k2.6", "object": "model"},
        {"id": "moonshotai/kimi-k3", "object": "model"},
        {"id": "nvidia/nemotron-3-ultra-550b-a55b", "object": "model"},
        {"id": "nvidia/nemotron-3.5-lightning-30b-a3b", "object": "model"},
        {"id": "openai/gpt-oss-120b", "object": "model"},
        {"id": "google/gemma-4-31b-it", "object": "model"},
        # listed upstream but NOT chat-completions models -> excluded
        {"id": "nvidia/embed-qa-4", "object": "model"},
        {"id": "nvidia/nemotron-3.5-content-safety", "object": "model"},
        {"id": "nvidia/riva-translate-4b-instruct", "object": "model"},
        {"id": "nvidia/nemotron-parse", "object": "model"},
        {"id": "nvidia/neva-22b", "object": "model"},
        {"id": "nvidia/nvclip", "object": "model"},
        {"id": "snowflake/arctic-embed-l", "object": "model"},
        {"id": "meta/llama-guard-4-12b", "object": "model"},
        {"id": "nvidia/nemotron-4-340b-reward", "object": "model"},
        {"id": "nvidia/cosmos-reason2-8b", "object": "model"},
        {"id": "nvidia/ai-synthetic-video-detector", "object": "model"},
    ]
}


class TestNvidiaNim:
    def test_no_api_key_returns_empty(self):
        assert nvidia_nim.fetch("https://integrate.api.nvidia.com/v1", api_key=None) == []

    @patch("requests.get")
    def test_chat_catalog_included_non_chat_excluded(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE)
        models = nvidia_nim.fetch("https://integrate.api.nvidia.com/v1", api_key="nvapi-test")
        ids = {m.id for m in models}
        assert ids == {
            "deepseek-v4-flash", "deepseek-v4-pro", "minimax-m3",
            "kimi-k2.6", "kimi-k3", "nemotron-3-ultra-550b-a55b",
            "nemotron-3.5-lightning-30b-a3b", "gpt-oss-120b", "gemma-4-31b-it",
        }

    @patch("requests.get")
    def test_kimi_k3_included(self, mock_get):
        """K3 IS listed on the live NIM catalog as of 2026-09-02 (verified
        against the authenticated /v1/models response). The historical
        exclusion here was correct then -- NIM didn't host it -- and is
        stale now. Usability verification is probe.verify_models' job in
        refresh(), not this allowlist-free module's."""
        mock_get.return_value = _mock_response(SAMPLE)
        models = nvidia_nim.fetch("https://integrate.api.nvidia.com/v1", api_key="nvapi-test")
        by_id = {m.id: m for m in models}
        assert by_id["kimi-k3"].upstream_id == "moonshotai/kimi-k3"

    @patch("requests.get")
    def test_reasoning_flag_from_id_heuristic(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE)
        models = nvidia_nim.fetch("https://integrate.api.nvidia.com/v1", api_key="nvapi-test")
        by_id = {m.id: m for m in models}
        assert by_id["nemotron-3-ultra-550b-a55b"].reasoning is True
        assert by_id["gpt-oss-120b"].reasoning is True
        assert by_id["minimax-m3"].reasoning is False

    @patch("requests.get")
    def test_sends_bearer_auth(self, mock_get):
        mock_get.return_value = _mock_response({"data": []})
        nvidia_nim.fetch("https://integrate.api.nvidia.com/v1", api_key="nvapi-abc")
        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer nvapi-abc"

    @patch("requests.get")
    def test_fetch_failure_returns_empty(self, mock_get):
        mock_get.side_effect = ConnectionError("boom")
        assert nvidia_nim.fetch("https://integrate.api.nvidia.com/v1", api_key="nvapi-test") == []

    @patch("requests.get")
    def test_only_non_chat_catalog_entries_yield_empty(self, mock_get):
        """Under the all-free funnel a bare chat-shaped id IS included
        (usability is probe's job); only non-chat endpoints are filtered
        here. So an upstream response containing solely an embed model
        yields nothing."""
        mock_get.return_value = _mock_response({"data": [{"id": "nvidia/embed-qa-4"}]})
        models = nvidia_nim.fetch("https://integrate.api.nvidia.com/v1", api_key="nvapi-test")
        assert models == []

    @patch("requests.get")
    def test_dated_variant_matches_canonical(self, mock_get):
        """deepseek-ai/deepseek-v4-flash-0731 (dated build on the live API)
        must be picked up, with id stripped to deepseek-v4-flash (so it
        matches the HIGH tier logical_id) and upstream_id kept as the
        full dated path (so the proxy sends the right name to NVIDIA)."""
        mock_get.return_value = _mock_response({"data": [
            {"id": "deepseek-ai/deepseek-v4-flash-0731", "context_length": 1048576},
        ]})
        models = nvidia_nim.fetch("https://integrate.api.nvidia.com/v1", api_key="nvapi-test")
        assert len(models) == 1
        m = models[0]
        assert m.id == "deepseek-v4-flash", f"id should be canonical, got {m.id}"
        assert m.upstream_id == "deepseek-ai/deepseek-v4-flash-0731", f"upstream_id must keep full dated path, got {m.upstream_id}"

    @patch("requests.get")
    def test_sends_user_agent_header(self, mock_get):
        mock_get.return_value = _mock_response({"data": []})
        nvidia_nim.fetch("https://integrate.api.nvidia.com/v1", api_key="nvapi-test")
        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["User-Agent"] == "open-free-router/0.1"
