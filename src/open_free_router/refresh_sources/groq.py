#!/usr/bin/env python3
"""Groq free models source."""
from __future__ import annotations

from typing import List

import requests

from open_free_router.registry import ModelInfo


SOURCE_NAME = "groq"

# Known free Groq models. Groq free tier changes occasionally; keep list conservative.
#
# CORRECTION 2026-08-02 (same day, deeper follow-up pass): the previous
# version of this comment marked llama-3.3-70b-versatile and
# llama-3.1-8b-instant as "confirmed current" based on Groq's docs code
# samples -- those samples turned out to be stale relative to Groq's own
# dedicated deprecations page (console.groq.com/docs/deprecations),
# which is a stronger signal (it's the actual lifecycle tracker, not
# just an example that hadn't been updated yet). That page states both
# models were deprecated via email notice on 2026-06-17, with a shutdown
# date of 2026-08-16 -- two weeks out from when this was written.
# Lesson: for a fast-moving provider, "appears in a docs code sample" is
# weaker evidence than "appears on the dedicated deprecations page,"
# and the latter should be checked even when the former looks fine.
#
# Removed (this pass): llama-3.3-70b-versatile, llama-3.1-8b-instant
# Removed (previous pass, still valid): gemma2-9b-it, mixtral-8x7b-32768
#
# Not a live authenticated /v1/models call -- this sandbox can't reach
# Groq's API. Run
# `curl https://api.groq.com/openai/v1/models -H "Authorization: Bearer $KEY"`
# against a real key to confirm before fully trusting this list.
KNOWN_FREE = [
    "openai/gpt-oss-20b",       # confirmed current + free: Groq's own recommended replacement
                                 # for llama-3.1-8b-instant; exact ID from console.groq.com/docs/overview
    "openai/gpt-oss-120b",      # confirmed current + free: Groq's own recommended replacement
                                 # for llama-3.3-70b-versatile; exact ID + reasoning capability
                                 # confirmed via console.groq.com/docs/model/openai/gpt-oss-120b
    "llama-guard-3-8b",         # LOWER CONFIDENCE, unchanged from previous pass: not found on
                                 # Groq's deprecations page (good sign) but also not seen in any
                                 # "current free lineup" listing checked so far -- some community
                                 # trackers reference "Llama Guard 4 12B" as current, but no
                                 # first-party exact ID was found for it, so not swapped in on a
                                 # guess.
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
