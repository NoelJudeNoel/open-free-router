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
# to paid-only on the Gemini Developer API as of 2026-04-01.
#
# gemini-2.5-flash / gemini-2.5-flash-lite REMOVED 2026-08-02 (same day,
# deeper follow-up pass): Google's own official deprecations page
# (ai.google.dev/gemini-api/docs/deprecations) lists an October 16, 2026
# shutdown date for both -- but real-world reports on Google's own
# developer forum describe both returning 404 "no longer available"
# starting July 9, 2026, weeks *before* that stated date, with no
# changelog announcement. "Shutdown dates are the earliest possible
# dates" per Google's own caveat -- evidently that cuts both ways, and a
# provider's stated future deprecation date is not a floor you can rely
# on for "still definitely works today."
#
# Replaced with gemini-3.6-flash / gemini-3.5-flash-lite (CORRECTION: an
# earlier version of this list used gemini-3.5-flash, but a deeper research
# pass found that gemini-3.6-flash is the current Flash main-line default,
# released 2026-07-21 and now superseding 3.5 Flash as the default). The
# version-number asymmetry (Flash at 3.6, Flash-Lite still at 3.5) is
# intentional on Google's part -- explicitly called out by multiple
# independent sources as Google's own naming, NOT a typo -- so Flash-Lite
# stays on 3.5.
#
# gemini-3.6-flash free-tier availability has an explicit citation: a
# third-party aggregator quotes Google's current Gemini Developer API
# pricing as listing "Free Tier Standard usage for Gemini 3.6 Flash"
# (5+ independent sources, all within 1-2 weeks of this change).
# gemini-3.5-flash-lite's free status is inferred by continuity (Flash-Lite
# has consistently been Google's free/cheap tier) rather than an equally
# explicit per-model citation -- slightly lower confidence on that one.
#
# Structural gap worth naming regardless of which exact IDs are current:
# unlike nous.py/sensenova.py, there's no live pricing signal to
# auto-detect a change like this from, and unlike nvidia_nim.py, there's
# no provider-published catalog page with a "Free" filter either -- the
# free/paid split (and whether a model is even still callable) lives
# only in prose documentation and forum reports, so it can only be
# caught by periodic manual research like this, not by any live API
# check even with a real key.
KNOWN_FREE = [
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
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
