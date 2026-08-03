"""Tests for tiered fallback routing (tier/high, tier/mid, tier/low)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from open_free_router.config import Config
from open_free_router.registry import Registry, ModelInfo, ProviderConfig
from open_free_router.tiers import (
    TIERS, TIER_IDS, is_tier_id, tier_members, UpstreamInstance,
)
from open_free_router.upstream import (
    TierExhaustedError, TierStreamResult, reset_tier_state, _router,
    forward_tier_buffered, forward_tier_streaming, _is_retryable_status,
    _Cooldown,
)


@pytest.fixture()
def registry() -> Registry:
    return Registry.load(Config().registry_path)


@pytest.fixture(autouse=True)
def _reset_cooldown():
    reset_tier_state()
    yield
    reset_tier_state()


class _FakeResp:
    def __init__(self, status, body=b"{}", headers=None, retry_after=None):
        self.status = status
        self._body = body
        self._headers = headers or {}
        self._retry_after = retry_after

    def read(self):
        return self._body

    def getheader(self, name):
        if name.lower() == "retry-after":
            return self._retry_after
        return self._headers.get(name)

    def getheaders(self):
        # Return list of (name, value) tuples like http.client.HTTPResponse
        out = []
        for k, v in self._headers.items():
            out.append((k, v))
        if self._retry_after:
            out.append(("Retry-After", self._retry_after))
        return out


def _make_provider(name="sensenova", models=None, url="https://api.sen", key="sk-sen", prefix="sen"):
    return ProviderConfig(name=name, upstream_url=url, api_key=key,
                          prefix=prefix, models=models or [])


def _glm_model(ctx=128000, max_tokens=4096):
    return ModelInfo(id="glm-5.2", upstream_id="z-ai/glm-5.2",
                     context_window=ctx, max_tokens=max_tokens, reasoning=False)


# tier id recognition
def test_tier_ids_present():
    assert set(TIER_IDS) == {"tier/high", "tier/mid", "tier/low"}


@pytest.mark.parametrize("mid", ["tier/high", "tier/mid", "tier/low"])
def test_is_tier_id_true(mid):
    assert is_tier_id(mid)


@pytest.mark.parametrize("mid", ["nv/glm-5.2", "glm-5.2", "z-ai/glm-5.2", ""])
def test_is_tier_id_false(mid):
    assert not is_tier_id(mid)


# tier mapping contents (design spec)
def test_high_tier_logical_ids():
    assert TIERS["high"] == ["glm-5.2", "deepseek-v4-flash", "gemini-3.6-flash"]


def test_mid_tier_logical_ids():
    assert set(TIERS["mid"]) == {
        "minimax-m3", "step-3.7-flash", "laguna-s-2.1", "laguna-xs-2.1",
        "mimo-v2.5-free", "ling-3.0-flash", "nemotron-3-ultra-550b-a55b",
    }


def test_low_tier_empty_by_default():
    assert TIERS["low"] == []


# pool expansion (incl. fuzzy upstream_id matching)
def test_high_pool_contains_expected_instances(registry):
    pool = tier_members("high", registry)
    keys = {f"{p.provider.name}/{p.model.id}" for p in pool}
    assert "sensenova/glm-5.2" in keys
    assert "nvidia-nim/z-ai/glm-5.2" in keys
    assert "sensenova/deepseek-v4-flash" in keys
    assert "google-ai-studio/gemini-3.6-flash" in keys


def test_mid_pool_fuzzy_upstream_id_match(registry):
    pool = tier_members("mid", registry)
    keys = {f"{p.provider.name}/{p.model.id}" for p in pool}
    assert "nvidia-nim/minimaxai/minimax-m3" in keys


def test_pool_priority_orders_best_first(registry):
    """Within the glm-5.2 logical model, the 1M-context sensenova instance
    must be attempted before the 128k nvidia-nim one (context tiebreaker)."""
    pool = tier_members("high", registry)
    glm = [p for p in pool
           if p.model.id == "glm-5.2" or p.upstream_model == "z-ai/glm-5.2"]
    assert glm, "expected at least one glm-5.2 instance in high tier"
    # the first glm instance should be the largest-context one
    assert glm[0].context_window >= glm[-1].context_window
    assert glm[0].context_window == 1_048_576  # sensenova 1M
    assert glm[0].provider.name == "sensenova"


def test_low_tier_is_catchall(registry):
    low = tier_members("low", registry)
    assert len(low) > 0


# context-window pre-filter
def test_context_prefilter_excludes_small_window(registry):
    pool = tier_members("high", registry, request_context=2_000_000)
    assert all(p.context_window >= 2_000_000 for p in pool) is (len(pool) >= 0)
    assert not any(p.provider.name == "sensenova"
                   and p.context_window == 1_048_576 for p in pool)


def test_context_prefilter_allows_small_request(registry):
    pool = tier_members("high", registry, request_context=0)
    assert len(pool) >= 4


# cooldown behaviour
def test_cooldown_skips_instance(registry):
    pool = tier_members("high", registry)
    target = pool[0]
    _router.cooldowns.mark(target.key, retry_after="9999")
    assert _router.cooldowns.in_cooldown(target.key)


def test_cooldown_expires():
    cd = _Cooldown()
    cd.mark("x", retry_after="0.01")
    assert cd.in_cooldown("x")
    import time as _t
    _t.sleep(0.02)
    assert not cd.in_cooldown("x")


# retryable status classification
@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_retryable_codes(code):
    assert _is_retryable_status(code)


@pytest.mark.parametrize("code", [200, 400, 401, 403, 404])
def test_non_retryable_codes(code):
    assert not _is_retryable_status(code)


# forward_tier_buffered: failover + exhaust
def _single_inst_registry():
    p = _make_provider()
    m = _glm_model()
    p.models = [m]
    reg = MagicMock()
    reg.providers = {"sensenova": p}
    return reg, p, m


def test_forward_buffered_success_on_first():
    reg, p, m = _single_inst_registry()
    inst = UpstreamInstance.for_provider(p, m)
    with patch("open_free_router.tiers.tier_members", return_value=[inst]), \
         patch("open_free_router.upstream._connect") as mc:
        mc.return_value = (_FakeResp(200, b'{"ok":true}'), MagicMock())
        status, body, hdrs = forward_tier_buffered(
            "high", reg, {"model": "tier/high", "_endpoint_path": "chat/completions"}, 5)
        assert status == 200
        assert json.loads(body) == {"ok": True}


def test_forward_buffered_retries_then_fails_over():
    reg, p, m = _single_inst_registry()
    inst_a = UpstreamInstance.for_provider(p, m)
    p2 = _make_provider(name="nvidia-nim", url="https://api.nv", key="sk-nv", prefix="nv")
    m2 = ModelInfo(id="glm-5.2", upstream_id="z-ai/glm-5.2", context_window=128000,
                   max_tokens=4096, reasoning=False)
    p2.models = [m2]
    inst_b = UpstreamInstance.for_provider(p2, m2)
    with patch("open_free_router.tiers.tier_members",
               return_value=[inst_a, inst_b]), \
         patch("open_free_router.upstream._connect") as mc:
        # inst_a: 429 with retry_after -> cooldown+break immediately (no retry);
        # inst_b: 200 -> success
        mc.side_effect = [
            (_FakeResp(429, retry_after="0"), MagicMock()),
            (_FakeResp(200, b'{"ok":"b"}'), MagicMock()),
        ]
        status, body, hdrs = forward_tier_buffered(
            "high", reg, {"model": "tier/high", "_endpoint_path": "chat/completions"}, 5)
        assert status == 200
        assert json.loads(body) == {"ok": "b"}
        assert _router.cooldowns.in_cooldown(inst_a.key)


def test_forward_buffered_exhausts_pool():
    reg, p, m = _single_inst_registry()
    inst = UpstreamInstance.for_provider(p, m)
    with patch("open_free_router.tiers.tier_members", return_value=[inst]), \
         patch("open_free_router.upstream._connect") as mc:
        mc.return_value = (_FakeResp(401), MagicMock())
        with pytest.raises(TierExhaustedError) as ei:
            forward_tier_buffered(
                "high", reg, {"model": "tier/high", "_endpoint_path": "chat/completions"}, 5)
        assert ei.value.tier == "high"
        assert ei.value.last_status == 401


def test_forward_buffered_cooldown_skips_instance():
    reg, p, m = _single_inst_registry()
    inst = UpstreamInstance.for_provider(p, m)
    _router.cooldowns.mark(inst.key, retry_after="0")
    with patch("open_free_router.tiers.tier_members", return_value=[inst]), \
         patch("open_free_router.upstream._connect") as mc:
        with pytest.raises(TierExhaustedError):
            forward_tier_buffered(
                "high", reg, {"model": "tier/high", "_endpoint_path": "chat/completions"}, 5)
        mc.assert_not_called()


# streaming switch logic
def test_forward_streaming_error_then_switch():
    reg, p, m = _single_inst_registry()
    inst_a = UpstreamInstance.for_provider(p, m)
    p2 = _make_provider(name="nvidia-nim", url="https://api.nv", key="sk-nv", prefix="nv")
    m2 = ModelInfo(id="glm-5.2", upstream_id="z-ai/glm-5.2", context_window=128000,
                   max_tokens=4096, reasoning=False)
    p2.models = [m2]
    inst_b = UpstreamInstance.for_provider(p2, m2)
    with patch("open_free_router.tiers.tier_members",
               return_value=[inst_a, inst_b]), \
         patch("open_free_router.upstream._connect") as mc:
        # inst_a: 503 (retryable) -> retry; 503 again -> cooldown; inst_b: 200
        mc.side_effect = [
            (_FakeResp(503, b'{"e":"rate"}'), MagicMock()),
            (_FakeResp(503, b'{"e":"rate"}'), MagicMock()),
            (_FakeResp(200, b""), MagicMock()),
        ]
        res = forward_tier_streaming(
            "high", reg,
            {"model": "tier/high", "_endpoint_path": "chat/completions", "stream": True}, 5)
        result, inst = res
        assert result.status == 200
        assert result.switch_allowed is False
        assert inst.key == inst_b.key
        assert _router.cooldowns.in_cooldown(inst_a.key)


# proxy integration
def test_proxy_tier_exhausted_returns_429():
    from open_free_router.proxy import _ProxyHandler
    reg = MagicMock()
    reg.providers = {}
    handler = _ProxyHandler.__new__(_ProxyHandler)
    handler.registry = reg
    handler._upstream_timeout = 5
    req = {"model": "tier/high", "messages": [{"role": "user", "content": "hi"}]}
    with patch("open_free_router.tiers.tier_members", return_value=[]), \
         patch.object(handler, "_send_json") as sj:
        handler._forward_request("chat/completions", json.dumps(req))
        assert sj.call_args.args[0] == 429
        assert "exhausted" in sj.call_args.args[1]["error"]["message"]


def test_proxy_models_list_includes_tiers():
    from open_free_router.proxy import _ProxyHandler
    reg = MagicMock()
    reg.providers = {}
    handler = _ProxyHandler.__new__(_ProxyHandler)
    handler.registry = reg
    handler._upstream_timeout = 5
    with patch.object(handler, "_send_json") as sj:
        handler._handle_list_models()
        data = sj.call_args.args[1]["data"]
        ids = {d["id"] for d in data}
        assert "tier/high" in ids and "tier/mid" in ids and "tier/low" in ids
