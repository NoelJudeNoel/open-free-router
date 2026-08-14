"""Tests for tiered fallback routing (tier/high, tier/mid, tier/low)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from open_free_router.registry import Registry, ModelInfo, ProviderConfig
from open_free_router.tiers import (
    TIERS, TIER_IDS, is_tier_id, tier_members, tier_cascade_pool, UpstreamInstance,
)
from open_free_router.upstream import (
    TierExhaustedError, TierStreamResult, reset_tier_state, _router,
    forward_tier_buffered, forward_tier_streaming, _is_retryable_status,
    _Cooldown,
)

DEFAULT_YAML = Path(__file__).parent.parent / "src" / "open_free_router" / "registry.default.yaml"


@pytest.fixture()
def registry() -> Registry:
    """Load the actual shipped registry.default.yaml, not
    Config().registry_path (the *user's* real config file location).

    The previous version of this fixture used Config().registry_path,
    which only resolves to a populated registry on a machine that
    already has open-free-router configured with real providers --
    it's empty (or missing entirely) on any clean environment,
    including this repo's own CI. That's exactly what happened: this
    fixture made 7 of this file's tests fail in CI (confirmed against
    the real GitHub Actions run for this fix) while appearing to pass
    for whoever wrote them locally, because their own machine happened
    to have a real config file with real providers configured. Loading
    the shipped default template instead makes these tests reproducible
    on any machine, dev or CI, with no user-specific state required.
    """
    data = yaml.safe_load(DEFAULT_YAML.read_text())
    return Registry(data)


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
        "mimo-v2.5", "nemotron-3-ultra-550b-a55b",
    }


def test_low_tier_empty_by_default():
    assert TIERS["low"] == []


# normalization: :free and -free suffix stripping
@pytest.mark.parametrize("uid,expected", [
    ("stepfun/step-3.7-flash:free", "step-3.7-flash"),
    ("step-3.7-flash:free", "step-3.7-flash"),
    ("deepseek-v4-flash-free", "deepseek-v4-flash"),
    ("poolside/laguna-s-2.1:free", "laguna-s-2.1"),
    ("z-ai/glm-5.2", "glm-5.2"),
    ("mimo-v2.5-free", "mimo-v2.5"),
    ("m1:free", "m1"),
    ("glm-5.2", "glm-5.2"),
    # date suffixes: -0731 (MMDD), -20250814 (YYYYMMDD), etc.
    ("deepseek-ai/deepseek-v4-flash-0731", "deepseek-v4-flash"),
    ("deepseek-v4-flash-0731", "deepseek-v4-flash"),
    ("deepseek-v4-flash-20250814", "deepseek-v4-flash"),
])
def test_normalize_strips_suffixes(uid, expected):
    from open_free_router.tiers import _normalize
    assert _normalize(uid) == expected, f"{uid} -> {_normalize(uid)} != {expected}"


def test_suffix_variants_expand_into_tier_pool(registry):
    """Free-tier variants with -free suffixes must be matched by their
    tier"s logical_id, so they are available as failover candidates
    within the same tier instead of falling through to the low catch-all.
    The default.yaml template has opencode-zen-free models (with -free
    suffixes) but not Nous models (auto-refreshed); the Nous :free variant
    is tested via the same normalization logic in _normalize()."""
    # high tier: deepseek-v4-flash should match the opencode-zen-free
    # variant (deepseek-v4-flash-free) via suffix stripping
    high = tier_members("high", registry)
    high_keys = {i.key for i in high}
    assert "opencode-zen-free/deepseek-v4-flash-free" in high_keys, "zen -free variant missing from high -- suffix stripping failed"
    # mid tier: laguna-s-2.1 should match the opencode-zen-free variant
    # (laguna-s-2.1-free)
    mid = tier_members("mid", registry)
    mid_keys = {i.key for i in mid}
    assert "opencode-zen-free/laguna-s-2.1-free" in mid_keys, "zen -free variant missing from mid -- suffix stripping failed"
    # Before the fix, these -free variants were silently excluded from their
    # tiers and only reachable (out of order) in the low catch-all pool.


# pool expansion (incl. fuzzy upstream_id matching)
def test_high_pool_contains_expected_instances(registry):
    pool = tier_members("high", registry)
    keys = {f"{p.provider.name}/{p.model.id}" for p in pool}
    assert "sensenova/glm-5.2" in keys
    # model.id is the local short id (see registry.default.yaml), not the
    # fully-qualified upstream_id -- nvidia-nim's local id for this model
    # is "glm-5.2", same as sensenova's, distinguished by provider name.
    assert "nvidia-nim/glm-5.2" in keys
    assert "sensenova/deepseek-v4-flash" in keys
    assert "google-ai-studio/gemini-3.6-flash" in keys


def test_mid_pool_fuzzy_upstream_id_match(registry):
    pool = tier_members("mid", registry)
    keys = {f"{p.provider.name}/{p.model.id}" for p in pool}
    # "minimax-m3" is nvidia-nim's local id for upstream_id
    # "minimaxai/minimax-m3" -- this is the "fuzzy" match this test name
    # refers to: the tier's logical id ("minimax-m3") matches via
    # _normalize() stripping the "minimaxai/" prefix from the upstream_id,
    # not via an exact upstream_id string match.
    assert "nvidia-nim/minimax-m3" in keys


def test_connect_prepends_upstream_path_prefix(registry):
    """Regression: the tier path must keep the upstream URL's path prefix
    (e.g. '/v1' in https://token.sensenova.cn/v1) when building the request
    path — otherwise sensenova/nvidia/google-ai-studio all return 404.
    Mirrors proxy.py's ``f"{upstream_url}/{endpoint_suffix}"`` string
    concatenation.
    """
    from open_free_router.upstream import _connect

    captured = {}

    class _NoNetHTTPS:
        def __init__(self, host, port=443, timeout=120):
            captured["host"] = host
        def connect(self):
            captured["connected"] = True
        @property
        def sock(self):
            class _S:
                def setsockopt(self, *a, **k):
                    pass
            return _S()
        @sock.setter
        def sock(self, v):
            pass
        def request(self, method, path, body=None, headers=None):
            captured["method"] = method
            captured["path"] = path
        def getresponse(self):
            raise AssertionError("should not be reached")
        def close(self):
            pass

    # sensenova upstream_url is https://token.sensenova.cn/v1 -> base /v1
    sensenova = registry.providers["sensenova"]
    glm_model = next(m for m in sensenova.models if m.id == "glm-5.2")
    inst = UpstreamInstance.for_provider(sensenova, glm_model)
    with patch("open_free_router.upstream.http.client.HTTPSConnection", _NoNetHTTPS):
        with pytest.raises(AssertionError):
            _connect(inst, "/chat/completions", b"{}", {}, 5)
    assert captured["path"] == "/v1/chat/completions", captured
    assert captured["host"] == "token.sensenova.cn"

    # google-ai-studio upstream is https://generativelanguage.googleapis.com/v1beta
    # -> base /v1beta (a distinctively non-"/v1" prefix, so this exercises
    # something the sensenova check above wouldn't catch on its own)
    gai = registry.providers["google-ai-studio"]
    gai_inst = UpstreamInstance.for_provider(gai, gai.models[0])
    captured.clear()
    with patch("open_free_router.upstream.http.client.HTTPSConnection", _NoNetHTTPS):
        with pytest.raises(AssertionError):
            _connect(gai_inst, "/chat/completions", b"{}", {}, 5)
    assert captured["path"] == "/v1beta/chat/completions", captured


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


@pytest.mark.parametrize("value,expected_min", [
    ("0.1", 0),
    ("5", 5),
    ("120", 120),
])
def test_parse_retry_after_delta_seconds(value, expected_min):
    """delta-seconds Retry-After (the common case) is parsed as a float."""
    parsed = _Cooldown._parse_retry_after(value)
    assert parsed is not None and parsed >= expected_min


def test_parse_retry_after_http_date():
    """HTTP-date format Retry-After (e.g. \"Wed, 21 Oct 2099 07:28:00 GMT\")
    must be parsed too -- some upstreams return dates, not bare seconds.
    Previously this raised ValueError and silently fell back to the default
    60s cooldown instead of honoring the real window."""
    from datetime import datetime, timezone, timedelta
    future = (datetime.now(timezone.utc) + timedelta(seconds=30)).strftime(
        "%a, %d %b %Y %H:%M:%S GMT")
    parsed = _Cooldown._parse_retry_after(future)
    assert parsed is not None
    assert 25 <= parsed <= 35


def test_parse_retry_after_invalid_returns_none():
    """Garbage Retry-After values fall back to the default cooldown (60s) via
    the `or DEFAULT_COOLDOWN` in _Cooldown.mark() -- _parse_retry_after
    itself must return None so mark() can apply the fallback."""
    assert _Cooldown._parse_retry_after("not-a-date-or-number") is None
    assert _Cooldown._parse_retry_after("") is None
    assert _Cooldown._parse_retry_after(None) is None


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
        mc.return_value = (_FakeResp(200, b'{"ok":true, "model":"tier/high"}'), MagicMock())
        status, body, hdrs, returned_inst = forward_tier_buffered(
            "high", reg, {"model": "tier/high", "_endpoint_path": "chat/completions"}, 5)
        assert status == 200
        parsed = json.loads(body)
        assert parsed["ok"] is True
        # Observability layer 1: the client-facing "model" field must be
        # rewritten from the requested tier alias to the actual serving
        # instance, so an agent showing "current model: <field>" reflects
        # reality instead of the opaque "tier/high" alias.
        assert parsed["model"] == inst.key
        assert returned_inst is inst


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
        status, body, hdrs, returned_inst = forward_tier_buffered(
            "high", reg, {"model": "tier/high", "_endpoint_path": "chat/completions"}, 5)
        assert status == 200
        parsed = json.loads(body)
        assert parsed["ok"] == "b"
        # The client-facing model field must reflect whichever instance
        # actually served the request (inst_b, the failover target), not
        # the one that failed (inst_a).
        assert parsed["model"] == inst_b.key
        assert returned_inst is inst_b
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


# ── Regression tests for bugs found in a post-hoc review of this feature ──

def test_instance_priority_actually_orders_when_context_ties(registry):
    """The bug this guards: _INSTANCE_PRIORITY used to never match
    anything (tuple field order didn't match how it was destructured at
    lookup time), so every instance silently got the same default
    priority and ordering only ever came from the context_window
    tiebreaker. Construct a registry where two instances of the same
    logical model have IDENTICAL context_window -- if priority weren't
    actually working, sort order between them would be arbitrary
    (whatever order the registry dict happens to iterate in); with a
    working priority table, the higher-priority provider must come
    first regardless of dict iteration order."""
    from open_free_router.tiers import _INSTANCE_PRIORITY, _expand_logical

    # Confirm the priority table itself is populated the way callers expect:
    # a real (logical_id, provider_name) lookup must NOT hit the 1000
    # fallback for entries actually listed.
    assert _INSTANCE_PRIORITY[("glm-5.2", "sensenova")] == 0
    assert _INSTANCE_PRIORITY[("glm-5.2", "nvidia-nim")] == 1

    reg = Registry({
        "z-provider": {  # sorts after "sensenova" alphabetically/by-dict-order
            "upstream_url": "https://example.com/v1",
            "models": [{"id": "glm-5.2", "context_window": 999999}],
        },
        "sensenova": {
            "upstream_url": "https://token.sensenova.cn/v1",
            "models": [{"id": "glm-5.2", "context_window": 999999}],  # tie
        },
    })
    results = _expand_logical("glm-5.2", reg)
    priorities = {provider.name: priority for priority, _key, provider, _model in results}
    # sensenova is explicitly prioritized; z-provider isn't listed at all
    # (falls back to 1000) -- sensenova must sort first despite z-provider
    # having no context_window disadvantage and a "later" provider name.
    assert priorities["sensenova"] < priorities["z-provider"]
    ordered_providers = [provider.name for _p, _k, provider, _m in
                          sorted(results, key=lambda t: (t[0], t[1]))]
    assert ordered_providers[0] == "sensenova"


def test_patch_model_strips_internal_routing_keys():
    """The bug this guards: _patch_model() used to do a plain dict(req)
    shallow copy, so internal keys proxy.py stashes on the request
    (_endpoint_path, and _headers if ever populated) rode along into
    json.dumps() and got sent as real fields in the body forwarded to
    the actual third-party upstream provider."""
    from open_free_router.upstream import _patch_model
    from open_free_router.tiers import UpstreamInstance

    provider = ProviderConfig(name="sensenova", upstream_url="https://token.sensenova.cn/v1",
                               models=[ModelInfo(id="glm-5.2", upstream_id="glm-5.2")])
    inst = UpstreamInstance.for_provider(provider, provider.models[0])
    req = {
        "model": "tier/high",
        "messages": [{"role": "user", "content": "hi"}],
        "_endpoint_path": "chat/completions",
        "_headers": {"X-Original": "value"},
    }
    patched = _patch_model(req, inst)
    assert "_endpoint_path" not in patched
    assert "_headers" not in patched
    assert patched["model"] == "glm-5.2"
    # only the real request fields must survive
    assert patched["messages"] == req["messages"]
    # confirm this is what actually gets serialized and sent upstream,
    # not just what _patch_model happens to return
    serialized = json.loads(json.dumps(patched))
    assert "_endpoint_path" not in serialized
    assert "_headers" not in serialized


def test_forward_tier_streaming_success_returns_closeable_raw_conn():
    """The bug this guards: TierStreamResult used to only keep the
    HTTPResponse (confusingly in a field named `conn`), discarding the
    real HTTPConnection/HTTPSConnection returned by _connect() without
    ever closing it -- a slow socket leak under sustained tier-streaming
    traffic. response.close() alone doesn't reliably release the
    underlying connection's socket, so both must be reachable and the
    real connection must be independently closeable."""
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.getheaders.return_value = [("Content-Type", "text/event-stream")]
    fake_conn = MagicMock()

    provider = ProviderConfig(name="sensenova", upstream_url="https://token.sensenova.cn/v1",
                               models=[ModelInfo(id="glm-5.2", upstream_id="glm-5.2")])
    reg = Registry({})
    reg.providers["sensenova"] = provider

    with patch("open_free_router.upstream._connect", return_value=(fake_resp, fake_conn)):
        req = {"model": "tier/high", "messages": []}
        result = forward_tier_streaming("high", reg, req, timeout=5)

    res, inst = result
    assert res.response is fake_resp
    assert res.raw_conn is fake_conn
    # proxy.py's cleanup path calls res.raw_conn.close(), not res.response.close()
    res.raw_conn.close()
    fake_conn.close.assert_called_once()


def test_every_tier_member_resolves_to_at_least_one_instance(registry):
    """The bug this guards: TIERS["mid"] used to list "ling-3.0-flash",
    which matched nothing in the real registry (Zen's actual id has a
    "-free" suffix; the one manual entry that would have matched under
    Nous was removed elsewhere as a confirmed-broken mapping) -- a dead
    tier member that silently shrank the pool with no error or warning.
    Guards every current tier (except "low", which is a deliberate
    catch-all with no fixed logical-id list) against this recurring."""
    from open_free_router.tiers import _expand_logical

    for tier_name in ("high", "mid"):
        for logical_id in TIERS[tier_name]:
            matches = _expand_logical(logical_id, registry)
            assert matches, (
                f"TIERS[{tier_name!r}] lists {logical_id!r}, which matches "
                f"zero instances in registry.default.yaml -- dead tier member"
            )


def test_forward_tier_streaming_exhaustion_carries_last_status():
    """The bug this guards: forward_tier_streaming() used to return a
    bare None on pool exhaustion, so the client-facing error always said
    "last HTTP None" even when every instance had actually failed with a
    real status code -- inconsistent with (and less useful than)
    forward_tier_buffered()'s TierExhaustedError(tier, last_status)."""
    provider = ProviderConfig(name="sensenova", upstream_url="https://token.sensenova.cn/v1",
                               models=[ModelInfo(id="glm-5.2", upstream_id="glm-5.2")])
    reg = Registry({})
    reg.providers["sensenova"] = provider

    fake_resp = MagicMock()
    fake_resp.status = 503
    fake_resp.read.return_value = b'{"error": "unavailable"}'
    fake_resp.getheader.return_value = None
    fake_resp.getheaders.return_value = []
    fake_conn = MagicMock()

    with patch("open_free_router.upstream._connect", return_value=(fake_resp, fake_conn)):
        req = {"model": "tier/high", "messages": []}
        with pytest.raises(TierExhaustedError) as exc_info:
            forward_tier_streaming("high", reg, req, timeout=5, num_retries=0)

    assert exc_info.value.last_status == 503


def test_rebuild_proxy_index_resets_tier_cooldowns():
    """The bug this guards: reset_tier_state() existed with a docstring
    saying to call it after a registry rebuild, but nothing in
    production code actually did -- only tests called it directly to
    reset state between cases."""
    from open_free_router.proxy import rebuild_proxy_index

    _router.cooldowns.mark("sensenova/glm-5.2", "60")
    assert _router.cooldowns.in_cooldown("sensenova/glm-5.2") is True
    rebuild_proxy_index()
    assert _router.cooldowns.in_cooldown("sensenova/glm-5.2") is False


# ── Tier routing observability (three layers: per-request model field,
# aggregate stats via tier_status(), event log lines) ──

def test_buffered_success_records_stats():
    reg, p, m = _single_inst_registry()
    inst = UpstreamInstance.for_provider(p, m)
    with patch("open_free_router.tiers.tier_members", return_value=[inst]), \
         patch("open_free_router.upstream._connect") as mc:
        mc.return_value = (_FakeResp(200, b'{"ok":true}'), MagicMock())
        forward_tier_buffered(
            "high", reg, {"model": "tier/high", "_endpoint_path": "chat/completions"}, 5)
    snap = _router.stats.snapshot()
    assert snap[inst.key] == {"success": 1, "failure": 0}


def test_buffered_failure_records_stats():
    reg, p, m = _single_inst_registry()
    inst = UpstreamInstance.for_provider(p, m)
    with patch("open_free_router.tiers.tier_members", return_value=[inst]), \
         patch("open_free_router.upstream._connect") as mc:
        mc.return_value = (_FakeResp(500), MagicMock())
        with pytest.raises(TierExhaustedError):
            forward_tier_buffered(
                "high", reg, {"model": "tier/high", "_endpoint_path": "chat/completions"},
                5, num_retries=0)
    snap = _router.stats.snapshot()
    assert snap[inst.key]["failure"] >= 1
    assert snap[inst.key]["success"] == 0


def test_streaming_success_records_stats():
    provider = ProviderConfig(name="sensenova", upstream_url="https://token.sensenova.cn/v1",
                               models=[ModelInfo(id="glm-5.2", upstream_id="glm-5.2")])
    reg = Registry({})
    reg.providers["sensenova"] = provider
    inst_key = "sensenova/glm-5.2"

    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.getheaders.return_value = [("Content-Type", "text/event-stream")]
    fake_conn = MagicMock()

    with patch("open_free_router.upstream._connect", return_value=(fake_resp, fake_conn)):
        forward_tier_streaming("high", reg, {"model": "tier/high", "messages": []}, timeout=5)

    snap = _router.stats.snapshot()
    assert snap[inst_key] == {"success": 1, "failure": 0}


def test_tier_status_reflects_stats_and_cooldown(registry):
    from open_free_router.upstream import tier_status

    inst_key = "sensenova/glm-5.2"
    _router.stats.record_success(inst_key)
    _router.stats.record_success(inst_key)
    _router.stats.record_failure(inst_key)
    _router.cooldowns.mark(inst_key, "30")

    snapshot = tier_status(registry)
    assert "high" in snapshot
    entry = next(e for e in snapshot["high"] if e["instance"] == inst_key)
    assert entry["success"] == 2
    assert entry["failure"] == 1
    assert entry["in_cooldown"] is True
    assert entry["cooldown_seconds_remaining"] is not None
    assert 0 < entry["cooldown_seconds_remaining"] <= 30

    # An instance with no recorded activity yet must still appear, with
    # zeroed counters -- tier_status() should describe the whole pool,
    # not just instances that happen to have been used already.
    other = next(e for e in snapshot["high"] if e["instance"] != inst_key)
    assert other["success"] == 0
    assert other["failure"] == 0
    assert other["in_cooldown"] is False
    assert other["cooldown_seconds_remaining"] is None


def test_reset_tier_state_clears_stats_too():
    """reset_tier_state() must clear stats, not just cooldowns -- counts
    recorded against a provider/model identity that a registry rebuild
    has since changed or removed aren't meaningful to keep around."""
    _router.stats.record_success("sensenova/glm-5.2")
    reset_tier_state()
    assert _router.stats.snapshot() == {}


def test_cooldown_event_is_logged(capsys):
    """Layer 3: a human-readable [tier] log line when an instance is
    cooled down, so 'what happened and why' is visible without needing
    to correlate timestamps against /api/status snapshots."""
    reg, p, m = _single_inst_registry()
    inst = UpstreamInstance.for_provider(p, m)
    with patch("open_free_router.tiers.tier_members", return_value=[inst]), \
         patch("open_free_router.upstream._connect") as mc:
        mc.return_value = (_FakeResp(500), MagicMock())
        with pytest.raises(TierExhaustedError):
            forward_tier_buffered(
                "high", reg, {"model": "tier/high", "_endpoint_path": "chat/completions"},
                5, num_retries=0)
    out = capsys.readouterr().out
    assert "[tier:-]" in out
    assert inst.key in out
    assert "cooling down" in out


def test_exhaustion_event_is_logged(capsys):
    reg, p, m = _single_inst_registry()
    inst = UpstreamInstance.for_provider(p, m)
    with patch("open_free_router.tiers.tier_members", return_value=[inst]), \
         patch("open_free_router.upstream._connect") as mc:
        mc.return_value = (_FakeResp(500), MagicMock())
        with pytest.raises(TierExhaustedError):
            forward_tier_buffered(
                "high", reg, {"model": "tier/high", "_endpoint_path": "chat/completions"},
                5, num_retries=0)
    out = capsys.readouterr().out
    assert "exhausted" in out
    assert "'high'" in out


def test_rewrite_model_field_falls_back_on_unparseable_body():
    """_rewrite_model_field() must never raise or corrupt an unexpected
    response body -- a non-JSON 2xx from a misbehaving upstream must
    still be forwarded to the client unchanged, not dropped."""
    from open_free_router.upstream import _rewrite_model_field
    garbage = b"not json at all"
    assert _rewrite_model_field(garbage, "sensenova/glm-5.2") == garbage


# ── Cross-tier cascade failover (the fix for "free quota exhausted -> app
# must manually switch + resend 'continue'") ──

def test_tier_cascade_pool_orders_requested_tier_first():
    """The requested tier's instances must all appear in the cascade pool
    and come before any lower-tier instance -- cascade is a fallback,
    never an escalation, so a tier/high request still prefers high
    instances whenever any are available."""
    reg = Registry(yaml.safe_load(DEFAULT_YAML.read_text()))
    high = tier_members("high", reg)
    cascade = tier_cascade_pool("high", reg)
    high_keys = [i.key for i in high]
    cascade_keys = [i.key for i in cascade]
    assert high_keys == cascade_keys[:len(high_keys)]
    for k in high_keys:
        assert k in cascade_keys
    mid_low = [k for k in cascade_keys if k not in set(high_keys)]
    if mid_low:
        assert cascade_keys.index(mid_low[0]) >= len(high_keys)


def test_forward_buffered_cascades_to_lower_tier():
    """When every instance of the requested tier rate-limits (429), the
    same request must be transparently served by a lower-tier instance
    instead of raising TierExhaustedError -- the app gets a normal 200
    and never sees the rate-limit error."""
    reg = Registry(yaml.safe_load(DEFAULT_YAML.read_text()))
    high_keys = {i.key for i in tier_members("high", reg)}

    def fake_connect(inst, path, data, headers, timeout):
        if inst.key in high_keys:
            return (_FakeResp(429, retry_after="0"), MagicMock())
        return (_FakeResp(200, b'{"ok":true,"model":"tier/high"}'), MagicMock())

    with patch("open_free_router.upstream._connect", side_effect=fake_connect):
        status, body, hdrs, inst = forward_tier_buffered(
            "high", reg, {"model": "tier/high", "_endpoint_path": "chat/completions"}, 5)
    assert status == 200
    assert inst.key not in high_keys
    parsed = json.loads(body)
    assert parsed["model"] == inst.key


def test_forward_streaming_cascades_to_lower_tier():
    """Streaming variant: a tier/high request whose high-tier instances
    all 429 must still begin streaming from a lower-tier instance (the
    switch happens before the first byte, so the client is unaware)."""
    reg = Registry(yaml.safe_load(DEFAULT_YAML.read_text()))
    high_keys = {i.key for i in tier_members("high", reg)}

    def fake_connect(inst, path, data, headers, timeout):
        if inst.key in high_keys:
            return (_FakeResp(429, retry_after="0"), MagicMock())
        return (_FakeResp(200, b""), MagicMock())

    with patch("open_free_router.upstream._connect", side_effect=fake_connect):
        res, inst = forward_tier_streaming(
            "high", reg,
            {"model": "tier/high", "_endpoint_path": "chat/completions", "stream": True}, 5)
    assert res.status == 200
    assert inst.key not in high_keys


def test_forward_buffered_no_cascade_stops_at_requested_tier():
    """With cascade disabled, a fully rate-limited requested tier must NOT
    spill into lower tiers -- it should exhaust and raise, preserving the
    opt-out semantics for callers that want strict single-tier behavior."""
    reg = Registry(yaml.safe_load(DEFAULT_YAML.read_text()))
    high_keys = {i.key for i in tier_members("high", reg)}

    def fake_connect(inst, path, data, headers, timeout):
        if inst.key in high_keys:
            return (_FakeResp(429, retry_after="0"), MagicMock())
        return (_FakeResp(200, b"{}"), MagicMock())

    with patch("open_free_router.upstream._connect", side_effect=fake_connect):
        with pytest.raises(TierExhaustedError):
            forward_tier_buffered(
                "high", reg, {"model": "tier/high", "_endpoint_path": "chat/completions"},
                5, cascade=False)

# ── Tier observability: TierTrace per-request trail (T0/T1/T2 design) ──

def test_trace_records_successful_request():
    """A successful tier request records ok attempt + served_by."""
    from open_free_router.upstream import TierTrace
    reg = Registry(yaml.safe_load(DEFAULT_YAML.read_text()))
    trace = TierTrace(trace_id="abc123", tier="high", request_context=1000)
    with patch("open_free_router.upstream._connect",
               return_value=(_FakeResp(200, b"{}"), MagicMock())):
        forward_tier_buffered("high", reg,
                              {"model": "tier/high", "_endpoint_path": "chat/completions"},
                              5, trace=trace)
    assert trace.served_by is not None
    assert len(trace.attempts) == 1
    assert trace.attempts[0]["outcome"] == "ok"
    assert trace.attempts[0]["status"] == 200


def test_trace_records_failover_and_cooldown():
    """Facade fails -> cooldown set + next instance tried, all in trail."""
    from open_free_router.upstream import TierTrace
    reg = Registry(yaml.safe_load(DEFAULT_YAML.read_text()))
    high_keys = {i.key for i in tier_members("high", reg)}
    trace = TierTrace(trace_id="f002", tier="high")

    def fake_connect(inst, path, data, headers, timeout):
        if inst.key in high_keys:
            return (_FakeResp(429, retry_after="41"), MagicMock())
        return (_FakeResp(200, b"{}"), MagicMock())

    with patch("open_free_router.upstream._connect", side_effect=fake_connect):
        status, body, hdrs, inst = forward_tier_buffered(
            "high", reg, {"model": "tier/high", "_endpoint_path": "chat/completions"},
            5, num_retries=0, trace=trace)
    assert status == 200
    # the failed high instance was recorded as error + marked cooldown
    failed_keys = [a["instance"] for a in trace.attempts if a["outcome"] == "error"]
    assert any(k in high_keys for k in failed_keys)
    assert any(a["retry_after"] == 41 for a in trace.attempts)
    assert trace.cooldowns_set, "failed instance should be in cooldowns_set"
    assert trace.served_by == inst.key


def test_trace_records_cascade_path_and_filtered():
    """Cascade path populated; context-window-filtered instances listed."""
    from open_free_router.upstream import TierTrace
    reg = Registry(yaml.safe_load(DEFAULT_YAML.read_text()))
    trace = TierTrace(trace_id="c009", tier="high", cascade=True)
    # absurd request_context filters every instance in every tier; the
    # pool would be empty -> exhaust, but the trail still records all
    # filtered entries via tier_filtered_instances
    with patch("open_free_router.upstream._connect",
               return_value=(_FakeResp(200, b"{}"), MagicMock())):
        with pytest.raises(TierExhaustedError) as ei:
            forward_tier_buffered(
                "high", reg,
                {"model": "tier/high", "_endpoint_path": "chat/completions"},
                5, request_context=10**15, trace=trace)
    assert trace.cascade_path == ["high", "mid", "low"]
    assert trace.filtered_keys, "expect context-window filtering recorded"
    assert all(a["outcome"] == "filtered" for a in trace.attempts)
    assert ei.value.trace is trace


def test_trace_records_streaming_success():
    from open_free_router.upstream import TierTrace
    reg = Registry(yaml.safe_load(DEFAULT_YAML.read_text()))
    trace = TierTrace(trace_id="s123", tier="high")
    with patch("open_free_router.upstream._connect",
               return_value=(_FakeResp(200, b""), MagicMock())):
        res, inst = forward_tier_streaming(
            "high", reg,
            {"model": "tier/high", "_endpoint_path": "chat/completions", "stream": True},
            5, trace=trace)
    assert res.status == 200
    assert trace.served_by == inst.key
    assert trace.attempts[-1]["outcome"] == "ok"


def test_trace_attached_to_exhaustion_error():
    from open_free_router.upstream import TierTrace
    reg = Registry(yaml.safe_load(DEFAULT_YAML.read_text()))
    high_keys = {i.key for i in tier_members("high", reg)}
    trace = TierTrace(trace_id="e777", tier="high")

    def fake_connect(inst, path, data, headers, timeout):
        return (_FakeResp(500, b"{}"), MagicMock())

    with patch("open_free_router.upstream._connect", side_effect=fake_connect):
        with pytest.raises(TierExhaustedError) as ei:
            forward_tier_buffered("high", reg,
                                  {"model": "tier/high", "_endpoint_path": "chat/completions"},
                                  5, num_retries=0, cascade=False, trace=trace)
    assert ei.value.trace is trace
    assert all(a["outcome"] == "error" for a in trace.attempts)
    assert ei.value.last_status == 500
    # every high instance should have been attempted and cooled down
    assert len(trace.cooldowns_set) == len(high_keys)

