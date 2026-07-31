#!/usr/bin/env python3
"""Refresh free model lists from provider APIs via pluggable sources."""
from __future__ import annotations

from typing import Dict

from open_free_router.registry import Registry

from open_free_router.refresh_sources import (
    openrouter,
    nvidia_nim,
    google_ai_studio,
    groq,
    deepseek,
    poolside,
)

# Map registry provider name -> refresh source module
SOURCE_MAP = {
    "openrouter": openrouter,
    "nvidia-nim": nvidia_nim,
    "google-ai-studio": google_ai_studio,
    "groq": groq,
    "deepseek": deepseek,
    "poolside": poolside,
}


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
