#!/usr/bin/env python3
"""Nous Research (Nous Portal) free models source.

Nous Portal's /v1/models returns OpenRouter-shaped entries (it proxies a
curated catalog), including a `pricing` object per model. Unlike
groq.py/nvidia_nim.py, we don't need a hand-maintained
KNOWN_FREE allowlist here — a model whose prompt AND completion price is
exactly 0 is free, so we can detect it directly from the API response.
This mirrors the pricing check openrouter.py already does.

NOTE: verified against a real (since-rotated) API key's live response on
2026-07-31 — confirmed /v1/models returns this OpenRouter-shaped schema
with a `pricing` field. Not covered by this repo's CI since it requires
network access + a Nous API key; run `open-free-router refresh --source
nous` manually after landing to confirm nothing has shifted upstream.
"""
from __future__ import annotations

from typing import List

import requests

from open_free_router.registry import ModelInfo


SOURCE_NAME = "nous"


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
        arch = m.get("architecture", {})
        input_modalities = arch.get("input_modalities", ["text"])
        output_modalities = arch.get("output_modalities", ["text"])
        if "text" not in output_modalities:
            continue  # skip TTS/ASR/image-only entries
        ctx = m.get("context_length", 131072) or 131072
        models.append(ModelInfo(
            id=mid,
            name=m.get("name", mid),
            context_window=int(ctx),
            max_tokens=min(int(ctx), 16384),
            reasoning=bool((m.get("reasoning") or {}).get("default_enabled", False)),
        ))

    print(f"  Found {len(models)} free {SOURCE_NAME} models")
    return models
