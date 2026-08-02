#!/usr/bin/env python3
"""StepFun free models source.

StepFun's /v1/models has no pricing field (confirmed against a real,
since-rotated API key's live response on 2026-07-31 — the response is
just {id, object, created, owned_by}), so unlike nous.py/sensenova.py we
can't auto-detect "free" from the API and need a hand-maintained
allowlist, same pattern as groq.py.

That same live check also caught that this repo's previously hardcoded
model, "step-3.7-flash", does not exist upstream — the actual model is
"step-3.5-flash". This is exactly the kind of drift auto-refresh is
meant to catch; it just needed a live check to notice.

Not covered by this repo's CI since it requires network access + a
StepFun API key; run `open-free-router refresh --source stepfun`
manually after landing, and periodically thereafter, since new
free-tier IDs won't be picked up automatically here the way they are
for nous/sensenova.
"""
from __future__ import annotations

from typing import List

import requests

from open_free_router.registry import ModelInfo

SOURCE_NAME = "stepfun"

# Confirmed present via live /v1/models on 2026-07-31. Update this list
# by hand if StepFun's free tier changes; there's no pricing field to
# detect it automatically.
KNOWN_FREE = [
    "step-3.5-flash",
]


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
        if mid not in KNOWN_FREE:
            continue
        models.append(ModelInfo(
            id=mid,
            name=mid,
            context_window=131072,
            max_tokens=16384,
            reasoning=False,  # unknown from /v1/models alone; no pricing/capability metadata to infer from
        ))

    print(f"  Found {len(models)} free {SOURCE_NAME} models")
    return models
