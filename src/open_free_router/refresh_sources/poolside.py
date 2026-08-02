#!/usr/bin/env python3
"""Poolside AI free models source.

Poolside offers free coding agent models (Laguna series) via their inference API.
API: https://inference.poolside.ai/v1
"""
from __future__ import annotations

from typing import List

import requests

from open_free_router.registry import ModelInfo


SOURCE_NAME = "poolside"

# Poolside free model ids.
#
# poolside/laguna-m.1 added 2026-08-02: confirmed free via multiple
# independent sources (VentureBeat, OpenRouter's own listing, a Puter.js
# tutorial with a working code sample) as of Poolside's own proprietary
# API, not just OpenRouter. Caveat worth keeping in mind: Poolside's own
# wording describes this as free "during a limited-time preview," not a
# permanent commitment -- unlike laguna-s-2.1/xs-2.1 below, which have
# no such caveat in the sources found for them.
KNOWN_FREE = [
    "poolside/laguna-s-2.1",
    "poolside/laguna-xs-2.1",
    "poolside/laguna-m.1",
]


def fetch(provider_base_url: str, api_key: str | None = None) -> List[ModelInfo]:
    """Fetch free models from Poolside AI inference API."""
    if not api_key:
        return []

    models: List[ModelInfo] = []
    try:
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

    for m in data.get("data", []):
        mid = m.get("id", "")
        if mid not in KNOWN_FREE:
            continue
        ctx = m.get("context_length", 262144) or 262144
        max_tokens = m.get("max_completion_tokens", 32768) or 32768
        features = m.get("supported_features", [])
        short_id = mid.split("/", 1)[-1] if "/" in mid else mid
        models.append(ModelInfo(
            id=short_id,
            upstream_id=mid,
            name=m.get("name", mid),
            context_window=int(ctx),
            max_tokens=min(int(max_tokens), 32768),
            reasoning="reasoning" in features,
        ))

    print(f"  Found {len(models)} free {SOURCE_NAME} models")
    return models
