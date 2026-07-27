#!/usr/bin/env python3
"""Google AI Studio (Gemini) free models source."""
from __future__ import annotations

from typing import List

import requests

from open_free_router.registry import ModelInfo


SOURCE_NAME = "google-ai-studio"

# These are representative free Gemini model ids on Google AI Studio.
# Google does not publish a stable single free-models-only endpoint here,
# so this source uses a conservative known-free set keyed from provider docs /
# community tracking. Adjust as Google changes free tiers.
KNOWN_FREE = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "gemma-3-27b-it",
]


def fetch(provider_base_url: str, api_key: str | None = None) -> List[ModelInfo]:
    if not api_key:
        return []

    models: List[ModelInfo] = []
    try:
        # Gemini API list models
        r = requests.get(
            f"{provider_base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ✗ {SOURCE_NAME} fetch failed: {e}")
        return models

    for m in data.get("models", []):
        mid = m.get("name", "")
        # Strip leading "models/" prefix when present
        if mid.startswith("models/"):
            mid = mid[len("models/"):]

        if mid not in KNOWN_FREE:
            continue
        ctx = m.get("inputTokenLimit", 131072) or m.get("contextWindow", 131072) or 131072
        models.append(ModelInfo(
            id=mid,
            name=m.get("displayName", mid),
            context_window=int(ctx),
            max_tokens=min(int(ctx), 8192),
            reasoning="pro" in mid or "flash" in mid,
        ))

    print(f"  Found {len(models)} free {SOURCE_NAME} models")
    return models
