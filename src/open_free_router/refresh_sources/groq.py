#!/usr/bin/env python3
"""Groq free models source."""
from __future__ import annotations

from typing import List

import requests

from open_free_router.registry import ModelInfo


SOURCE_NAME = "groq"

# Known free Groq models. Groq free tier changes occasionally; keep list conservative.
#
# Re-audited 2026-08-02 via web research (Groq's own docs code samples +
# multiple independent third-party trackers from Apr-Jun 2026), not a
# live authenticated /v1/models call -- this sandbox can't reach Groq's
# API. Confidence varies per entry, noted inline. Run
# `curl https://api.groq.com/openai/v1/models -H "Authorization: Bearer $KEY"`
# against a real key to confirm before fully trusting this list.
#
# Removed since the previous version of this list (multiple independent
# 2026 sources list Groq's current free lineup with zero mentions of
# either, and Mixtral in particular is a well-documented Groq
# deprecation):
#   - gemma2-9b-it
#   - mixtral-8x7b-32768
KNOWN_FREE = [
    "llama-3.3-70b-versatile",  # confirmed current: appears in Groq's own official docs code samples
    "llama-3.1-8b-instant",     # confirmed current: appears in Groq's own official docs code samples
    "llama-guard-3-8b",         # LOWER CONFIDENCE: absent from "top model" trackers, but those
                                 # typically exclude moderation models anyway, so absence isn't as
                                 # strong a signal here as it was for gemma2/mixtral above
    "openai/gpt-oss-20b",       # confirmed current: exact ID copied verbatim from Groq's own
                                 # official docs code sample (console.groq.com/docs/overview)
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
        ctx = m.get("context_window", 32768) or 32768
        models.append(ModelInfo(
            id=mid,
            name=m.get("name", mid),
            context_window=int(ctx),
            max_tokens=min(int(ctx), 16384),
            reasoning="guard" not in mid.lower(),
        ))

    print(f"  Found {len(models)} free {SOURCE_NAME} models")
    return models
