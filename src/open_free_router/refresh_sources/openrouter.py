#!/usr/bin/env python3
"""OpenRouter free models source."""
from __future__ import annotations

from typing import List

import requests

from open_free_router.registry import ModelInfo


SOURCE_NAME = "openrouter"

SKIP_PREFIXES = (
    "google/lyria",
    "nvidia/nemotron-3.5-content",
    "nvidia/nemotron-nano-12b-v2-vl",
    "nvidia/nemotron-nano-9b",
    "poolside/",
)


def fetch(provider_base_url: str, api_key: str | None = None) -> List[ModelInfo]:
    models: List[ModelInfo] = []
    try:
        r = requests.get(f"{provider_base_url}/models", timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ✗ {SOURCE_NAME} fetch failed: {e}")
        return models

    for m in data.get("data", []):
        mid = m["id"]
        if not mid.endswith(":free"):
            continue
        if any(mid.startswith(prefix) for prefix in SKIP_PREFIXES):
            continue
        pricing = m.get("pricing", {})
        if float(pricing.get("prompt", "1") or "1") != 0:
            continue
        arch = m.get("architecture", {})
        if "text" not in arch.get("input_modalities", ["text"]):
            continue
        models.append(ModelInfo(
            id=mid,
            name=m.get("name", mid),
            context_window=m.get("context_length", 131072) or 131072,
            max_tokens=min(m.get("context_length", 131072) or 131072, 16384),
            reasoning="nemotron-3-ultra" in mid or "nemotron-3-super" in mid,
        ))

    models.sort(key=lambda x: x.context_window, reverse=True)
    print(f"  Found {len(models)} free {SOURCE_NAME} models")
    return models
