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
)

# Map registry provider name -> refresh source module
SOURCE_MAP = {
    "openrouter": openrouter,
    "nvidia-nim": nvidia_nim,
    "google-ai-studio": google_ai_studio,
    "groq": groq,
    "deepseek": deepseek,
}


def refresh(reg: Registry, provider_name: str | None = None) -> Dict[str, bool]:
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
        results[name] = True

    return results
