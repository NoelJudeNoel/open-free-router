#!/usr/bin/env python3
"""NVIDIA NIM free models source."""
from __future__ import annotations

from typing import List

import requests

from open_free_router.registry import ModelInfo


SOURCE_NAME = "nvidia-nim"

# Verified 2026-08-02 against build.nvidia.com/models (NVIDIA's own
# catalog page, "Free Endpoint" filter) -- not a live authenticated
# /v1/models call, since this sandbox can't reach NVIDIA's API. Treat
# this as a lower-confidence check than an entry that's been running
# against the real API for a while: the catalog page and the API
# response aren't guaranteed to be perfectly in sync at every instant.
# All four pre-existing entries below were re-confirmed present with a
# "Free Endpoint" tag at the same time; two new ones were added
# (mistral-medium-3.5-128b, deepseek-ai/deepseek-v4-flash) that weren't
# previously in this list. Run `open-free-router refresh --source
# nvidia-nim` against a real key to confirm before relying on the new
# entries in production.
KNOWN_FREE = {
    "stepfun-ai/step-3.7-flash",
    "z-ai/glm-5.2",
    "minimaxai/minimax-m3",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "mistralai/mistral-medium-3.5-128b",
    "deepseek-ai/deepseek-v4-flash",
}


def fetch(provider_base_url: str, api_key: str | None = None) -> List[ModelInfo]:
    if not api_key:
        return []

    models: List[ModelInfo] = []
    try:
        r = requests.get(
            f"{provider_base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ✗ {SOURCE_NAME} fetch failed: {e}")
        return models

    for m in data.get("data", []):
        mid = m.get("id", "")
        if mid in KNOWN_FREE:
            models.append(ModelInfo(
                id=mid,
                name=mid.split("/")[-1],
                context_window=m.get("context_length", 131072) or 131072,
                max_tokens=16384,
                reasoning="nemotron" in mid.lower(),
            ))

    print(f"  Found {len(models)} free {SOURCE_NAME} models")
    return models
