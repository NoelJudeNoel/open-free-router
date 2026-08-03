"""Tests for refresh_sources/nvidia_nim.py -- previously had zero
dedicated coverage (only used as generic registry/proxy test fixture
data unrelated to its own fetch() logic). Sample payload below is
shaped to match NVIDIA NIM's real /v1/models response format
(id/object/created), based on the model IDs confirmed present on
build.nvidia.com/models (see the KNOWN_FREE comment in nvidia_nim.py
for what was and wasn't independently verified).
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
        {"id": "stepfun-ai/step-3.7-flash", "object": "model", "context_length": 131072},
        {"id": "z-ai/glm-5.2", "object": "model", "context_length": 1048576},
        {"id": "minimaxai/minimax-m3", "object": "model"},
        {"id": "nvidia/nemotron-3-ultra-550b-a55b", "object": "model"},
        {"id": "mistralai/mistral-medium-3.5-128b", "object": "model"},
        {"id": "deepseek-ai/deepseek-v4-flash", "object": "model"},
        {"id": "moonshotai/kimi-k2.6", "object": "model", "context_length": 262144},
        # present upstream but NOT in KNOWN_FREE -- must be excluded
        {"id": "nvidia/cosmos3-nano", "object": "model"},
        {"id": "deepseek-ai/deepseek-v4-pro", "object": "model"},
        # Kimi K3 does not exist on NVIDIA NIM as of this writing --
        # explicitly NOT added to KNOWN_FREE despite being requested;
        # see nvidia_nim.py's own comment for why. Included here to
        # guard against it ever silently sneaking into KNOWN_FREE on a
        # guess rather than a real confirmed listing.
        {"id": "moonshotai/kimi-k3", "object": "model"},
    ]
}


class TestNvidiaNim:
    def test_no_api_key_returns_empty(self):
        assert nvidia_nim.fetch("https://integrate.api.nvidia.com/v1", api_key=None) == []

    @patch("requests.get")
    def test_only_known_free_included(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE)
        models = nvidia_nim.fetch("https://integrate.api.nvidia.com/v1", api_key="nvapi-test")
        ids = {m.id for m in models}
        assert ids == nvidia_nim.KNOWN_FREE
        assert "nvidia/cosmos3-nano" not in ids  # exists upstream, not free
        assert "deepseek-ai/deepseek-v4-pro" not in ids  # sibling model, not the free -flash one

    @patch("requests.get")
    def test_newly_added_entries_are_present(self, mock_get):
        """Guards the specific two IDs added after the build.nvidia.com
        catalog check -- if either gets typo'd or removed from
        KNOWN_FREE without updating this test, this fails loudly."""
        mock_get.return_value = _mock_response(SAMPLE)
        models = nvidia_nim.fetch("https://integrate.api.nvidia.com/v1", api_key="nvapi-test")
        ids = {m.id for m in models}
        assert "mistralai/mistral-medium-3.5-128b" in ids
        assert "deepseek-ai/deepseek-v4-flash" in ids

    @patch("requests.get")
    def test_kimi_k2_6_included_k3_excluded(self, mock_get):
        """K2.6 is the newest Kimi actually free on NVIDIA NIM. K3 was
        requested but does not exist on NIM yet -- must not appear even
        though a plausible-looking entry for it is present in the mocked
        upstream response, guarding against ever adding it on a guess."""
        mock_get.return_value = _mock_response(SAMPLE)
        models = nvidia_nim.fetch("https://integrate.api.nvidia.com/v1", api_key="nvapi-test")
        ids = {m.id for m in models}
        assert "moonshotai/kimi-k2.6" in ids
        assert "moonshotai/kimi-k3" not in ids

    @patch("requests.get")
    def test_reasoning_flag_from_id_heuristic(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE)
        models = nvidia_nim.fetch("https://integrate.api.nvidia.com/v1", api_key="nvapi-test")
        by_id = {m.id: m for m in models}
        assert by_id["nvidia/nemotron-3-ultra-550b-a55b"].reasoning is True
        assert by_id["z-ai/glm-5.2"].reasoning is False

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
    def test_model_no_longer_present_upstream_yields_empty(self, mock_get):
        mock_get.return_value = _mock_response({"data": [{"id": "some-unrelated-model"}]})
        models = nvidia_nim.fetch("https://integrate.api.nvidia.com/v1", api_key="nvapi-test")
        assert models == []
