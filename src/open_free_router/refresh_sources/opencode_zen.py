#!/usr/bin/env python3
"""OpenCode Zen free models source.

Zen's /v1/models has no pricing field and (confirmed via a live,
unauthenticated check on 2026-07-31) requires no API key at all — it
returns Zen's full catalog, paid and free mixed together. Free entries
are identifiable by a "-free" ID suffix, with one exception: "big-pickle"
is free but doesn't follow that convention (confirmed present in the
same live check, alongside deepseek-v4-flash-free, mimo-v2.5-free,
ling-3.0-flash-free, nemotron-3-ultra-free, north-mini-code-free,
laguna-s-2.1-free).

That live check also caught that this repo's previously hardcoded
"v4-flash-free" entry doesn't exist upstream — the real ID is
"deepseek-v4-flash-free" — again, exactly the kind of drift auto-refresh
exists to catch.

Not covered by this repo's CI since it requires network access; run
`open-free-router refresh --source opencode-zen-free` manually after
landing to confirm nothing has shifted upstream.
"""
from __future__ import annotations

from typing import List

import requests

from open_free_router.registry import ModelInfo

SOURCE_NAME = "opencode-zen-free"

# Free models that don't follow the "-free" ID suffix convention.
EXTRA_FREE = {"big-pickle"}


def _is_free(model_id: str) -> bool:
    return model_id.endswith("-free") or model_id in EXTRA_FREE


def fetch(provider_base_url: str, api_key: str | None = None) -> List[ModelInfo]:
    # No API key required for Zen's public model list.
    models: List[ModelInfo] = []
    try:
        r = requests.get(f"{provider_base_url}/models", timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ✗ {SOURCE_NAME} fetch failed: {e}")
        return models

    for m in data.get("data", []):
        mid = m.get("id", "")
        if not mid or not _is_free(mid):
            continue
        models.append(ModelInfo(
            id=mid,
            name=mid,
            context_window=131072,
            max_tokens=16384,
            reasoning=False,  # unknown from /v1/models alone; no capability metadata to infer from
        ))

    print(f"  Found {len(models)} free {SOURCE_NAME} models")
    return models
