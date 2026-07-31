#!/usr/bin/env python3
"""SenseNova (SenseTime) free models source.

Like Nous Portal, SenseNova's /v1/models returns a `pricing` object per
model with prompt/completion/image/request/input_cache_read fields, all
"0" for genuinely free models — so we detect free models directly from
the API rather than a hand-maintained allowlist.

NOTE: verified against a real (since-rotated) API key's live response on
2026-07-31 — confirmed /v1/models returns this schema with all-zero
pricing strings on free entries (e.g. sensenova-6.7-flash-lite,
deepseek-v4-flash, glm-5.2). Not covered by this repo's CI since it
requires network access + a SenseNova API key; run `open-free-router
refresh --source sensenova` manually after landing to confirm nothing
has shifted upstream.
"""
from __future__ import annotations

from typing import List

import requests

from open_free_router.registry import ModelInfo


SOURCE_NAME = "sensenova"


def _is_free(pricing: dict) -> bool:
    try:
        prompt = float(pricing.get("prompt", "1") or "1")
        completion = float(pricing.get("completion", "1") or "1")
    except (TypeError, ValueError):
        return False
    return prompt == 0 and completion == 0


def fetch(provider_base_url: str, api_key: str | None = None) -> List[ModelInfo]:
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
        if not mid:
            continue
        if not _is_free(m.get("pricing", {})):
            continue
        if "text" not in m.get("output_modalities", ["text"]):
            continue
        ctx = m.get("context_length", 131072) or 131072
        max_out = m.get("max_output_length", 8192) or 8192
        features = m.get("supported_features", [])
        models.append(ModelInfo(
            id=mid,
            name=m.get("name", mid),
            context_window=int(ctx),
            max_tokens=min(int(max_out), 16384),
            reasoning="reasoning" in features,
        ))

    print(f"  Found {len(models)} free {SOURCE_NAME} models")
    return models
