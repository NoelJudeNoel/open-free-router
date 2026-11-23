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


def _price_zero(cost: dict) -> bool:
    """True when both input and output text prices are explicitly 0."""
    text = (cost or {}).get("text") or {}
    inp = (text.get("input") or {}).get("price")
    out = (text.get("output") or {}).get("price")
    if inp is None and out is None:
        return False
    try:
        return float(inp or 0) == 0 and float(out or 0) == 0
    except (TypeError, ValueError):
        return False


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
        if not mid:
            continue

        # 2026-11 schema: the catalog carries explicit cost/limit/capability
        # metadata. Prefer it; fall back to the old suffix convention when
        # a leaner response omits those fields.
        rich = isinstance(m.get("cost"), dict) and "type" in m
        if rich:
            if m.get("type") != "chat":
                continue
            if not _price_zero(m.get("cost")):
                continue
            if "text" not in ((m.get("modalities") or {}).get("output") or ["text"]):
                continue
            limit = m.get("limit") or {}
            ctx = limit.get("context") or 131072
            out = limit.get("output") or 16384
            caps = m.get("capabilities") or {}
            reasoning = bool((caps.get("reasoning") or {}).get("supported", False))
        else:
            if not _is_free(mid):
                continue
            ctx, out, reasoning = 131072, 16384, False

        models.append(ModelInfo(
            id=mid,
            name=m.get("name", mid),
            context_window=int(ctx),
            max_tokens=min(int(out), 16384),
            reasoning=reasoning,
        ))

    print(f"  Found {len(models)} free {SOURCE_NAME} models")
    return models
