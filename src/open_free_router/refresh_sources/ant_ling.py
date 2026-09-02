#!/usr/bin/env python3
"""Ant Group Ling free models source.

Ant Ling API endpoint: https://api.anting.com/v1
Ling is Ant Group's general-purpose language model series.
"""
from __future__ import annotations

from typing import List

import requests

from open_free_router.registry import ModelInfo


SOURCE_NAME = "ant-ling"

# Known free Ling models on Ant's API.
# Ling-3.0-flash is the latest flash-tier model in the Ling series.
KNOWN_FREE = [
    "Ling-3.0-flash",
    "Ling-3.0-flash-1T",
]


def fetch(provider_base_url: str, api_key: str | None = None) -> List[ModelInfo]:
    """Fetch free models from Ant Group Ling API."""
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

    # Ant Ling API returns models in various formats depending on version.
    # Try common response shapes.
    model_list = data.get("data", data.get("models", data.get("result", [])))
    if isinstance(model_list, dict):
        model_list = model_list.get("data", model_list.get("models", []))

    for m in model_list:
        mid = m.get("id", "")
        if not mid:
            continue
        # Normalize model ID - strip provider prefix if present
        if "/" in mid:
            mid = mid.split("/", 1)[-1]
        if mid not in KNOWN_FREE:
            continue

        ctx = m.get("context_length", m.get("context_window", 262144))
        if not ctx:
            ctx = 262144
        max_tokens = m.get("max_tokens", m.get("max_output_tokens", 16384))
        if not max_tokens:
            max_tokens = 16384

        models.append(ModelInfo(
            id=mid,
            name=m.get("name", mid),
            context_window=int(ctx),
            max_tokens=min(int(max_tokens), 16384),
            reasoning=False,
        ))

    print(f"  Found {len(models)} free {SOURCE_NAME} models")
    return models