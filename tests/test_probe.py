"""Tests for probe.py -- live chat-completion usability verification.

Born from the 2026-09-02 audit: NVIDIA NIM's /v1/models listed 54
chat-capable models of which only 9 answered a real request (38x 404
"Function not found", incl. production model kimi-k2.6), and two of
SenseNova's free listings 404'd. These tests pin the verdict rules and
the batch guards that keep an outage from wiping the registry.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from open_free_router import probe
from open_free_router.probe import (
    ALIVE, AUTH, DEAD, INCONCLUSIVE, VERIFIED,
    ProbeResult, probe_chat, verify_models,
)
from open_free_router.registry import ModelInfo


def _resp(status, json_body=None, text=""):
    r = MagicMock()
    r.status_code = status
    if json_body is not None:
        r.json.return_value = json_body
    else:
        r.json.side_effect = ValueError("no json")
    r.text = text
    return r


class TestClassify:
    @pytest.mark.parametrize("status,verdict", [
        (200, VERIFIED),
        (429, ALIVE),
        (500, ALIVE),
        (503, ALIVE),
        (400, DEAD),
        (404, DEAD),
        (401, AUTH),
        (403, AUTH),
        (301, INCONCLUSIVE),
    ])
    def test_status_buckets(self, status, verdict):
        assert probe._classify(status) == verdict


class TestProbeChat:
    @patch("open_free_router.probe.requests.post")
    def test_200_is_verified(self, mock_post):
        mock_post.return_value = _resp(200, {"choices": [{"message": {"content": "pong"}}]})
        res = probe_chat("https://x/v1", "key", "m1", timeout=1)
        assert res.verdict == VERIFIED and res.status == 200

    @patch("open_free_router.probe.requests.post")
    def test_429_and_503_are_alive_not_dead(self, mock_post):
        mock_post.return_value = _resp(429, {"error": {"message": "tpm exhausted"}})
        assert probe_chat("https://x/v1", "k", "m", timeout=1).verdict == ALIVE
        mock_post.return_value = _resp(503, {"error": {"message": "overloaded"}})
        assert probe_chat("https://x/v1", "k", "m", timeout=1).verdict == ALIVE

    @patch("open_free_router.probe.requests.post")
    def test_404_is_dead(self, mock_post):
        mock_post.return_value = _resp(404, {"detail": "Function ... Not found"})
        res = probe_chat("https://x/v1", "k", "m", timeout=1)
        assert res.verdict == DEAD

    @patch("open_free_router.probe.requests.post")
    def test_400_retried_with_fuller_payload(self, mock_post):
        """Strict upstreams reject tiny max_tokens; a 400 must get one
        retry with a fuller payload before being called dead."""
        mock_post.side_effect = [
            _resp(400, {"message": "missing tags"}),
            _resp(200, {"choices": [{"message": {"content": "pong"}}]}),
        ]
        res = probe_chat("https://x/v1", "k", "m", timeout=1)
        assert res.verdict == VERIFIED
        assert mock_post.call_count == 2
        assert mock_post.call_args_list[1].kwargs["json"]["max_tokens"] == 256

    @patch("open_free_router.probe.requests.post")
    def test_400_still_dead_when_retry_also_400(self, mock_post):
        mock_post.side_effect = [
            _resp(400, {"message": "bad"}),
            _resp(400, {"message": "still bad"}),
        ]
        assert probe_chat("https://x/v1", "k", "m", timeout=1).verdict == DEAD

    @patch("open_free_router.probe.requests.post")
    def test_401_is_auth(self, mock_post):
        mock_post.return_value = _resp(401, {"message": "invalid token"})
        assert probe_chat("https://x/v1", "k", "m", timeout=1).verdict == AUTH

    @patch("open_free_router.probe.requests.post")
    def test_timeout_is_inconclusive(self, mock_post):
        mock_post.side_effect = requests.ReadTimeout("timed out")
        res = probe_chat("https://x/v1", "k", "m", timeout=1)
        assert res.verdict == INCONCLUSIVE

    @patch("open_free_router.probe.requests.post")
    def test_sends_minimal_payload_and_ua(self, mock_post):
        mock_post.return_value = _resp(200, {"choices": []})
        probe_chat("https://x/v1", "sk-abc", "m1", timeout=1)
        kwargs = mock_post.call_args.kwargs
        assert mock_post.call_args.args[0] == "https://x/v1/chat/completions"
        assert kwargs["headers"]["Authorization"] == "Bearer sk-abc"
        assert kwargs["headers"]["User-Agent"] == "open-free-router/0.1"
        assert kwargs["json"]["stream"] is False
        assert kwargs["json"]["messages"] == [{"role": "user", "content": "Say pong"}]


def _models(*ids):
    return [ModelInfo(id=i) for i in ids]


def _stub(results_by_model):
    def fn(base_url, api_key, model, chat_path="/chat/completions", timeout=30):
        return results_by_model[model]
    return fn


class TestVerifyModels:
    def test_mixed_batch_keeps_order_drops_dead(self):
        results = {
            "a": ProbeResult("a", 200, VERIFIED),
            "b": ProbeResult("b", 404, DEAD),
            "c": ProbeResult("c", 429, ALIVE),
        }
        kept, res = verify_models("p", "https://x/v1", "k", _models("a", "b", "c"),
                                  delay=0, probe_fn=_stub(results))
        assert [m.id for m in kept] == ["a", "c"]
        assert [r.verdict for r in res] == [VERIFIED, DEAD, ALIVE]

    def test_all_auth_keeps_everything(self):
        """Rotated key: per-model verdicts are void, old list survives."""
        results = {m: ProbeResult(m, 401, AUTH) for m in ("a", "b")}
        kept, _ = verify_models("p", "https://x/v1", "k", _models("a", "b"),
                                delay=0, probe_fn=_stub(results))
        assert [m.id for m in kept] == ["a", "b"]

    def test_all_inconclusive_keeps_everything(self):
        results = {m: ProbeResult(m, 0, INCONCLUSIVE, "timeout") for m in ("a", "b")}
        kept, _ = verify_models("p", "https://x/v1", "k", _models("a", "b"),
                                delay=0, probe_fn=_stub(results))
        assert [m.id for m in kept] == ["a", "b"]

    def test_circuit_breaker_skips_after_repeated_timeouts(self):
        calls = []

        def fn(base_url, api_key, model, chat_path="/chat/completions", timeout=30):
            calls.append(model)
            return ProbeResult(model, 0, INCONCLUSIVE, "timeout")

        kept, res = verify_models("p", "https://x/v1", "k", _models(*"abcdefghij"),
                                  delay=0, probe_fn=fn)
        # first 5 probed, rest skipped but kept
        assert len(calls) == probe.MAX_CONSECUTIVE_INCONCLUSIVE
        assert len(res) == 10
        assert [m.id for m in kept] == list("abcdefghij")
        assert res[-1].detail.startswith("circuit breaker")

    def test_chat_path_override_for_google_ai_studio(self):
        captured = {}

        def fn(base_url, api_key, model, chat_path="/chat/completions", timeout=30):
            captured["path"] = chat_path
            return ProbeResult(model, 200, VERIFIED)

        verify_models("google-ai-studio", "https://g/v1beta", "k",
                      _models("gemini-3.7-flash"), delay=0, probe_fn=fn)
        assert captured["path"] == "/openai/chat/completions"

    def test_empty_candidates_short_circuits(self):
        kept, res = verify_models("p", "https://x/v1", "k", [], delay=0,
                                  probe_fn=lambda *a, **kw: None)
        assert kept == [] and res == []

    def test_uses_effective_upstream_id(self):
        """Dated NIM builds probe under their full upstream path, not the
        canonical short id."""
        seen = {}

        def fn(base_url, api_key, model, chat_path="/chat/completions", timeout=30):
            seen["model"] = model
            return ProbeResult(model, 200, VERIFIED)

        verify_models("nvidia-nim", "https://x/v1", "k",
                      [ModelInfo(id="deepseek-v4-flash",
                                 upstream_id="deepseek-ai/deepseek-v4-flash-0731")],
                      delay=0, probe_fn=fn)
        assert seen["model"] == "deepseek-ai/deepseek-v4-flash-0731"
