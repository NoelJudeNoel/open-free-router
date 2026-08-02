"""Regression tests for a real production failure reported via a Hermes
session log: two Nous manual model entries were unverified (one
confirmed broken -- Nous's own API 404'd the resolved upstream_id) and
SenseNova's deepseek-v4-flash model had a local short-id ("v4-flash")
that didn't match its real name, causing a user's reasonable model-ID
guess ("nova/deepseek-v4-flash") to 403 even though a matching model
was in fact registered under a different name.

These tests load the actual shipped registry.default.yaml (not a hand-
built fixture) so a regression here -- e.g. someone re-adding an
unverified Nous entry, or reintroducing a SenseNova short-id mismatch --
is caught against the real file people actually get on first run.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from open_free_router.registry import Registry

DEFAULT_YAML = Path(__file__).parent.parent / "src" / "open_free_router" / "registry.default.yaml"


def _load_default_registry() -> Registry:
    data = yaml.safe_load(DEFAULT_YAML.read_text())
    return Registry(data)


class TestNousNoUnverifiedEntries:
    def test_nous_has_no_models_pending_reverification(self):
        """Guards against re-adding an unverified manual entry without
        realizing that's what happened last time -- see the nous: block's
        own comment in registry.default.yaml for the full incident."""
        reg = _load_default_registry()
        nous = reg.get("nous")
        assert nous is not None
        assert nous.models == []

    def test_the_specific_confirmed_broken_id_is_gone(self):
        """ling-3.0-flash:free -> inclusionai/ling-3.0-flash:free 404'd
        against Nous's real API in production. If this ever comes back,
        it must not be the same unverified mapping."""
        reg = _load_default_registry()
        nous = reg.get("nous")
        ids = {m.id for m in nous.models}
        assert "ling-3.0-flash:free" not in ids


class TestSenseNovaNaming:
    def test_deepseek_model_uses_its_real_name_as_local_id(self):
        """The local id must match SenseNova's actual model name so a
        user's reasonable guess at the model ID (prefix + real name)
        resolves, instead of requiring knowledge of an arbitrary
        internal short-id like the old "v4-flash"."""
        reg = _load_default_registry()
        sensenova = reg.get("sensenova")
        ids = {m.id for m in sensenova.models}
        assert "deepseek-v4-flash" in ids
        assert "v4-flash" not in ids

    def test_prefix_plus_real_name_is_the_routable_form(self):
        """This is the exact form a user reasonably guessed in the
        production incident (nova/deepseek-v4-flash) -- confirm it's
        now one of the two matching id forms the proxy checks (bare id
        with prefix), not just theoretically present in the registry."""
        reg = _load_default_registry()
        sensenova = reg.get("sensenova")
        model = next(m for m in sensenova.models if m.id == "deepseek-v4-flash")
        assert f"{sensenova.model_prefix}/{model.id}" == "nova/deepseek-v4-flash"
