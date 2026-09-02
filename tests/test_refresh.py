"""Regression tests for refresh()'s return-value semantics.

Before this fix, refresh() returned True for any provider whose fetch
*succeeded*, even if the model list was byte-for-byte identical to what
was already stored. Every caller (serve.py's scheduler, ui.py's
/api/refresh, cli.py's `refresh` command) treats True as "something
changed, go save + re-sync agent configs" via `any(results.values())`.
That mismatch meant a normal no-op refresh cycle still triggered a full
registry backup + rewrite of every synced agent config file.
"""
from __future__ import annotations

import types

from open_free_router.refresh import refresh
from open_free_router.registry import ModelInfo, Registry


def _fake_source(models):
    """Build a fake refresh_sources module returning a fixed model list."""
    return types.SimpleNamespace(fetch=lambda provider_base_url, api_key: models)


def test_unchanged_models_report_false(monkeypatch):
    reg = Registry({
        "fake": {
            "upstream_url": "https://example.com/v1",
            "api_key": "sk-test",
            "models": [{"id": "m1"}, {"id": "m2"}],
        },
    })
    same_models = [ModelInfo(id="m1"), ModelInfo(id="m2")]
    monkeypatch.setitem(
        __import__("open_free_router.refresh", fromlist=["SOURCE_MAP"]).SOURCE_MAP,
        "fake", _fake_source(same_models),
    )
    results = refresh(reg, provider_name="fake")
    assert results["fake"] is False
    assert [m.id for m in reg.get("fake").models] == ["m1", "m2"]


def test_changed_models_report_true_and_update_registry(monkeypatch):
    reg = Registry({
        "fake": {
            "upstream_url": "https://example.com/v1",
            "api_key": "sk-test",
            "models": [{"id": "m1"}],
        },
    })
    new_models = [ModelInfo(id="m1"), ModelInfo(id="m2")]
    monkeypatch.setitem(
        __import__("open_free_router.refresh", fromlist=["SOURCE_MAP"]).SOURCE_MAP,
        "fake", _fake_source(new_models),
    )
    results = refresh(reg, provider_name="fake")
    assert results["fake"] is True
    assert [m.id for m in reg.get("fake").models] == ["m1", "m2"]


def test_failed_fetch_reports_false(monkeypatch):
    reg = Registry({
        "fake": {
            "upstream_url": "https://example.com/v1",
            "api_key": "sk-test",
            "models": [{"id": "m1"}],
        },
    })
    monkeypatch.setitem(
        __import__("open_free_router.refresh", fromlist=["SOURCE_MAP"]).SOURCE_MAP,
        "fake", _fake_source([]),  # empty = fetch failed / returned nothing
    )
    results = refresh(reg, provider_name="fake")
    assert results["fake"] is False
    # registry must be untouched on a failed fetch
    assert [m.id for m in reg.get("fake").models] == ["m1"]


def test_any_results_values_reflects_real_change_not_fetch_success():
    """Sanity check on the actual pattern used by serve.py / ui.py / cli.py:
    `any(results.values())` must be False when every provider's fetch
    succeeded but nothing actually changed."""
    results = {"a": False, "b": False, "c": False}
    assert any(results.values()) is False
    results["b"] = True
    assert any(results.values()) is True


# ── live probe verification wiring (probe.verify_models) ────────────────

def _install_source(monkeypatch, models):
    import open_free_router.refresh as refresh_mod
    monkeypatch.setitem(refresh_mod.SOURCE_MAP, "fake", _fake_source(models))


def test_verify_drops_probe_dead_models(monkeypatch):
    import open_free_router.refresh as refresh_mod
    reg = Registry({
        "fake": {"upstream_url": "https://example.com/v1", "api_key": "sk-test",
                 "models": [{"id": "m1"}, {"id": "m2"}]},
    })
    _install_source(monkeypatch, [ModelInfo(id="m1"), ModelInfo(id="m2"),
                                  ModelInfo(id="m3")])
    # m2 answers nothing (probe-dead), m3 is new and verified
    monkeypatch.setattr(
        refresh_mod, "verify_models",
        lambda name, url, key, models: ([ModelInfo(id="m1"), ModelInfo(id="m3")], []),
    )
    results = refresh(reg, provider_name="fake", verify=True)
    assert results["fake"] is True
    assert [m.id for m in reg.get("fake").models] == ["m1", "m3"]


def test_verify_never_wipes_on_unanimous_probe_failure(monkeypatch):
    """Every candidate failing the probe is the signature of a rotated key
    or an upstream outage, not of a dead model -- the existing list must
    survive and the cycle must report no change."""
    import open_free_router.refresh as refresh_mod
    reg = Registry({
        "fake": {"upstream_url": "https://example.com/v1", "api_key": "sk-test",
                 "models": [{"id": "m1"}, {"id": "m2"}]},
    })
    _install_source(monkeypatch, [ModelInfo(id="m1"), ModelInfo(id="m2")])
    monkeypatch.setattr(refresh_mod, "verify_models",
                        lambda name, url, key, models: ([], []))
    results = refresh(reg, provider_name="fake", verify=True)
    assert results["fake"] is False
    assert [m.id for m in reg.get("fake").models] == ["m1", "m2"]


def test_verify_false_skips_probe_entirely(monkeypatch):
    import open_free_router.refresh as refresh_mod
    reg = Registry({
        "fake": {"upstream_url": "https://example.com/v1", "api_key": "sk-test",
                 "models": [{"id": "m1"}]},
    })
    _install_source(monkeypatch, [ModelInfo(id="m1"), ModelInfo(id="m2")])

    def _must_not_run(*a, **kw):
        raise AssertionError("verify_models must not run when verify=False")

    monkeypatch.setattr(refresh_mod, "verify_models", _must_not_run)
    results = refresh(reg, provider_name="fake", verify=False)
    assert results["fake"] is True
    assert [m.id for m in reg.get("fake").models] == ["m1", "m2"]
