#!/usr/bin/env python3
"""DeepSeek free models source."""
from __future__ import annotations

from typing import List

import requests

from open_free_router.registry import ModelInfo


SOURCE_NAME = "deepseek"

# DeepSeek free-tier model ids. Keep conservative; add/remove as platform changes.
#
# NOT independently re-verified as part of the audit that fixed
# stepfun.py/opencode_zen.py's stale entries -- freshness unknown as of
# 2026-08-01. If you have a DeepSeek API key, running
# `curl https://api.deepseek.com/v1/models -H "Authorization: Bearer $KEY"`
# and diffing against this list would close that gap.
KNOWN_FREE = [
    "deepseek-chat",
    "deepseek-reasoner",
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
        ctx = m.get("context_window", 65536) or 65536
        models.append(ModelInfo(
            id=mid,
            name=m.get("name", mid),
            context_window=int(ctx),
            max_tokens=min(int(ctx), 16384),
            reasoning="reasoner" in mid,
        ))

    print(f"  Found {len(models)} free {SOURCE_NAME} models")
    return models
