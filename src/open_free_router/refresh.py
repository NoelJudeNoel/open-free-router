#!/usr/bin/env python3
"""Refresh free model lists from provider APIs."""
from __future__ import annotations

import time
from typing import Optional

import requests

from open_free_router.registry import Registry, ModelInfo


def refresh_openrouter(reg: Registry) -> bool:
    """Fetch free models from OpenRouter API via proxy."""
    p = reg.get("openrouter")
    if not p:
        return False

    proxy_base = p.base_url
    try:
        r = requests.get(f"{proxy_base}/models", timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ✗ OpenRouter fetch failed: {e}")
        return False

    skip_prefixes = ("google/lyria", "nvidia/nemotron-3.5-content",
                     "nvidia/nemotron-nano-12b-v2-vl", "nvidia/nemotron-nano-9b",
                     "poolside/")

    models = []
    for m in data.get("data", []):
        mid = m["id"]
        if not mid.endswith(":free"):
            continue
        if any(mid.startswith(prefix) for prefix in skip_prefixes):
            continue
        pricing = m.get("pricing", {})
        if float(pricing.get("prompt", "1") or "1") != 0:
            continue
        arch = m.get("architecture", {})
        if "text" not in arch.get("input_modalities", ["text"]):
            continue
        models.append(ModelInfo(
            id=mid,
            name=m.get("name", mid),
            context_window=m.get("context_length", 131072) or 131072,
            max_tokens=min(m.get("context_length", 131072) or 131072, 16384),
            reasoning="nemotron-3-ultra" in mid or "nemotron-3-super" in mid,
        ))

    models.sort(key=lambda x: x.context_window, reverse=True)
    print(f"  Found {len(models)} free OpenRouter models")
    changed = [m.id for m in models] != [m.id for m in p.models]
    if changed:
        reg.update_models("openrouter", models)
    return changed


def refresh_nvidia_nim(reg: Registry) -> bool:
    """Fetch free models from NVIDIA NIM API."""
    p = reg.get("nvidia-nim")
    if not p or not p.effective_key:
        return False

    headers = {"Authorization": f"Bearer {p.effective_key}"}
    try:
        r = requests.get(f"{p.upstream_url or p.base_url}/models", headers=headers, timeout=120)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ✗ NIM fetch failed: {e}")
        return False

    known_free = {
        "stepfun-ai/step-3.7-flash",
        "z-ai/glm-5.2",
        "minimaxai/minimax-m3",
        "nvidia/nemotron-3-ultra-550b-a55b",
    }

    models = []
    for m in data.get("data", []):
        mid = m.get("id", "")
        if mid in known_free:
            models.append(ModelInfo(
                id=mid,
                name=mid.split("/")[-1],
                context_window=m.get("context_length", 131072) or 131072,
                max_tokens=16384,
                reasoning="nemotron" in mid.lower(),
            ))

    print(f"  Found {len(models)} free NIM models")
    changed = [m.id for m in models] != [m.id for m in p.models]
    if changed:
        reg.update_models("nvidia-nim", models)
    return changed
