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
#
# gemini-2.5-pro REMOVED 2026-08-02: multiple independent sources from
# Apr-Jul 2026 confirm Google moved Pro-series models (2.5 Pro, 3.x Pro)
# to paid-only on the Gemini Developer API as of 2026-04-01. This is a
# structural gap in this file's design worth naming explicitly: unlike
# nous.py/sensenova.py, there's no live pricing signal to auto-detect
# this kind of change from, and unlike nvidia_nim.py, there's no
# provider-published catalog page with a "Free" filter either -- the
# free/paid split here lives only in prose documentation, so it can
# only be caught by periodic manual research like this, not by any
# live API check even with a real key.
#
# gemini-2.5-flash / gemini-2.5-flash-lite NOT re-verified as part of
# this same pass: recent (as of 2026-07-16) sources reference "Gemini
# 3.5 Flash Standard" / "Gemini 3.1 Flash-Lite Standard" as the current
# free lineup, suggesting the 2.5 generation may already be superseded
# -- but no source found gave an exact, confirmed API model-ID string
# for the 3.x generation, so these two entries are left as-is rather
# than guessed-replaced with an unconfirmed ID.
KNOWN_FREE = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
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
