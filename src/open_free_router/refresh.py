#!/usr/bin/env python3
"""Refresh free model lists from provider APIs via pluggable sources."""
from __future__ import annotations

from typing import Dict

from open_free_router.registry import Registry

from open_free_router.refresh_sources import (
    openrouter,
    nvidia_nim,
    google_ai_studio,
    poolside,
    nous,
    sensenova,
    opencode_zen,
)

# Map registry provider name -> refresh source module
SOURCE_MAP = {
    "openrouter": openrouter,
    "nvidia-nim": nvidia_nim,
    "google-ai-studio": google_ai_studio,
    "poolside": poolside,
    "nous": nous,
    "sensenova": sensenova,
    "opencode-zen-free": opencode_zen,
}


def _load_canonical_upstream_urls() -> Dict[str, str]:
    """Canonical upstream_url for each curated (SOURCE_MAP) provider,
    read from the shipped registry.default.yaml.

    This is the actual source of truth for "what URL does this known
    provider use" -- deriving from it instead of hand-maintaining a
    second copy of the same mapping here means the two can't drift
    apart. Used by Registry.add_provider() to pin upstream_url for
    known providers regardless of what a caller (UI/CLI) submits: an
    authenticated-but-malicious or simply mistaken POST /api/providers
    can no longer redirect a known provider's upstream_url to an
    attacker's server, since the submitted value for a known name is
    ignored outright rather than merely gated behind auth.
    """
    import yaml
    from pathlib import Path
    default_path = Path(__file__).parent / "registry.default.yaml"
    try:
        data = yaml.safe_load(default_path.read_text()) or {}
    except OSError:
        return {}
    return {
        name: cfg["upstream_url"]
        for name, cfg in data.items()
        if isinstance(cfg, dict) and cfg.get("upstream_url")
    }


CANONICAL_UPSTREAM_URLS: Dict[str, str] = _load_canonical_upstream_urls()


def refresh(reg: Registry, provider_name: str | None = None) -> Dict[str, bool]:
    """Refresh one or all providers' model lists.

    Returns a dict of provider name -> **did this provider's model list
    actually change**. This is deliberately *not* "did the fetch succeed" —
    callers (serve.py's scheduler, ui.py's /api/refresh, cli.py's
    `refresh` command) all use this dict via ``any(results.values())`` to
    decide whether a registry save + agent-config sync is warranted. A
    provider that fetched successfully but returned the same model list
    it already had must report False here, otherwise every scheduled
    refresh triggers a full registry backup + rewrite of every agent's
    synced config file even when nothing changed.
    """
    results: Dict[str, bool] = {}

    providers = [provider_name] if provider_name else list(reg.providers.keys())
    for name in providers:
        p = reg.get(name)
        if not p:
            results[name] = False
            continue

        source = SOURCE_MAP.get(name)
        if not source:
            print(f"  ⚠ No refresh source for provider: {name}")
            results[name] = False
            continue

        print(f"Refreshing {name}...")
        new_models = source.fetch(
            provider_base_url=p.upstream_url or p.base_url,
            api_key=p.effective_key,
        )

        if not new_models:
            print(f"  ⚠ {name} fetch returned no models")
            results[name] = False
            continue

        current_ids = [m.id for m in p.models]
        new_ids = [m.id for m in new_models]
        changed = current_ids != new_ids
        if changed:
            reg.update_models(name, new_models)
            print(f"  ✓ {name} updated: {len(new_models)} models")
        else:
            print(f"  ✓ {name} unchanged: {len(new_models)} models")
        results[name] = changed

    return results
