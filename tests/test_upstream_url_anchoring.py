"""Tests for Registry.add_provider() pinning upstream_url for known
(SOURCE_MAP) providers to the canonical value in registry.default.yaml,
regardless of what a caller submits.
"""
from __future__ import annotations

from open_free_router.registry import ModelInfo, ProviderConfig, Registry
from open_free_router.refresh import CANONICAL_UPSTREAM_URLS, SOURCE_MAP


class TestUpstreamUrlAnchoring:
    def test_canonical_urls_loaded_for_every_known_provider(self):
        """Sanity check the loader itself: every SOURCE_MAP entry should
        have a canonical URL, since registry.default.yaml is supposed to
        be the authoritative list of curated providers."""
        for name in SOURCE_MAP:
            assert name in CANONICAL_UPSTREAM_URLS, f"no canonical upstream_url for {name}"
            assert CANONICAL_UPSTREAM_URLS[name].startswith("https://")

    def test_malicious_upstream_url_on_known_provider_is_overridden(self):
        reg = Registry({})
        malicious = ProviderConfig(
            name="stepfun",
            upstream_url="https://attacker.example.com/v1",
            api_key="sk-real-key",
            models=[ModelInfo(id="step-3.5-flash")],
        )
        pinned = reg.add_provider(malicious)
        assert pinned is True
        assert reg.get("stepfun").upstream_url == CANONICAL_UPSTREAM_URLS["stepfun"]
        assert reg.get("stepfun").upstream_url != "https://attacker.example.com/v1"

    def test_correct_upstream_url_on_known_provider_not_flagged(self):
        reg = Registry({})
        p = ProviderConfig(
            name="stepfun",
            upstream_url=CANONICAL_UPSTREAM_URLS["stepfun"],
            api_key="sk-real-key",
            models=[ModelInfo(id="step-3.5-flash")],
        )
        pinned = reg.add_provider(p)
        assert pinned is False
        assert reg.get("stepfun").upstream_url == CANONICAL_UPSTREAM_URLS["stepfun"]

    def test_custom_unknown_provider_keeps_free_form_upstream_url(self):
        """A provider name that has no refresh_sources module (i.e. the
        user added it by hand, e.g. a self-hosted OpenAI-compatible
        endpoint) must NOT be anchored -- arbitrary upstream_url is the
        whole point of being able to add a custom provider."""
        reg = Registry({})
        p = ProviderConfig(
            name="my-self-hosted-llm",
            upstream_url="http://192.168.1.50:8000/v1",
            api_key="sk-local",
            models=[ModelInfo(id="local-model")],
        )
        pinned = reg.add_provider(p)
        assert pinned is False
        assert reg.get("my-self-hosted-llm").upstream_url == "http://192.168.1.50:8000/v1"

    def test_pinning_applies_on_every_add_not_just_first(self):
        """Editing an existing known provider (re-POSTing it) must be
        re-anchored every time, not just on first creation."""
        reg = Registry({})
        reg.add_provider(ProviderConfig(
            name="stepfun", upstream_url=CANONICAL_UPSTREAM_URLS["stepfun"],
            api_key="sk-1", models=[ModelInfo(id="step-3.5-flash")],
        ))
        pinned = reg.add_provider(ProviderConfig(
            name="stepfun", upstream_url="https://attacker.example.com/v1",
            api_key="sk-1", models=[ModelInfo(id="step-3.5-flash")],
        ))
        assert pinned is True
        assert reg.get("stepfun").upstream_url == CANONICAL_UPSTREAM_URLS["stepfun"]
