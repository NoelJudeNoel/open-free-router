#!/usr/bin/env python3
"""NVIDIA NIM free models source.

Funnel (2026-09-02 redesign): the whole NIM catalog is on NVIDIA's free
endpoint tier, so the old 7-entry KNOWN_FREE allowlist was retired -- it
was stale in both directions (still listed step-3.7-flash / glm-5.2 /
mistral-medium-3.5-128b which are NOT in the live catalog, and blocked
newer live models like kimi-k3). Detection is now:

  1. live /v1/models catalog (authenticated)
  2. minus non-chat endpoints (embed / rerank / guard / vision / OCR /
     translate / reward / video -- they don't serve chat completions)
  3. minus anything that fails the live chat-probe in probe.py
     (wired in by refresh.refresh(); this module only does steps 1-2)

Step 3 is not optional hygiene -- the same 2026-09-02 audit that produced
this redesign probed all 54 chat-capable catalog entries and only 9
answered 200; 38 of them 404'd with "Function ... Not found", INCLUDING
``moonshotai/kimi-k2.6``, which sat in our production registry as healthy
at the time. NIM's catalog keeps long-dead listings; only a real request
tells you what actually runs.
"""
from __future__ import annotations

import re
from typing import List

import requests

from open_free_router.registry import ModelInfo

# Same pattern as tiers._DATE_SUFFIX_RE -- strip trailing -MMDD / -YYYYMMDD
# date suffixes from model ids before canonicalization.
_DATE_SUFFIX_RE = re.compile(r"-\d{4,}$")


SOURCE_NAME = "nvidia-nim"

# Catalog entries that are not chat-completions models. Substring match on
# the lowercased id. Embed/rerank/safety/reward models answer other NIM
# endpoints, vision-only models need image payloads, and none of them can
# serve the chat completions this router forwards -- verified empirically
# on 2026-09-02 (the 404/"not a chat model" shape of the catalog matches
# this list exactly).
NON_CHAT_KEYWORDS = (
    "embed", "retriev", "rerank",       # embedding / retrieval models
    "guard", "safety", "reward",        # moderation / reward models
    "clip", "vision", "vlm",            # vision encoders / vision-only
    "riva",                             # speech / translate endpoints
    "deplot", "kosmos", "fuyu", "neva", "vila", "nv-ocr", "nemotron-parse",
    "cosmos",                           # world/video reasoning models
    "video",                            # synthetic-video-detector etc.
    "ising",                            # calibration research model
)


def _is_chat_model(mid: str) -> bool:
    low = mid.lower()
    return not any(k in low for k in NON_CHAT_KEYWORDS)


def fetch(provider_base_url: str, api_key: str | None = None) -> List[ModelInfo]:
    if not api_key:
        return []

    models: List[ModelInfo] = []
    try:
        r = requests.get(
            f"{provider_base_url}/models",
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": "open-free-router/0.1"},
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ✗ {SOURCE_NAME} fetch failed: {e}")
        return models

    for m in data.get("data", []):
        mid = m.get("id", "")
        # Strip -MMDD date suffixes (e.g. -0731) so dated builds canonicalize
        # to their base name; the full dated id is kept in upstream_id.
        canonical_mid = _DATE_SUFFIX_RE.sub("", mid)
        if not _is_chat_model(canonical_mid):
            continue
        short_id = canonical_mid.split("/")[-1]  # e.g. "deepseek-v4-flash"
        models.append(ModelInfo(
            id=short_id,
            upstream_id=mid,  # full API path WITH date suffix for forwarding
            # NIM's /v1/models exposes no context metadata; stay conservative
            # (tier context pre-filter then treats every NIM instance as 128k)
            context_window=m.get("context_length", 131072) or 131072,
            max_tokens=16384,
            reasoning="nemotron" in mid.lower() or "gpt-oss" in mid.lower(),
        ))

    print(f"  Found {len(models)} chat-capable {SOURCE_NAME} catalog models "
          f"(live-probe filtering happens in refresh)")
    return models
