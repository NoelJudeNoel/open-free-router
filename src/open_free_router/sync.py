#!/usr/bin/env python3
"""Sync registry to agent configs via pluggable adapters."""
from __future__ import annotations

import importlib
from pathlib import Path

from open_free_router.registry import Registry


BUILTIN_ADAPTERS = {
    "hermes": "open_free_router.adapters.hermes",
    "pi": "open_free_router.adapters.pi",
    "omp": "open_free_router.adapters.omp",
    "opencode": "open_free_router.adapters.opencode",
}


def sync_all(reg: Registry, agent_paths: dict[str, Path]):
    """Run each agent adapter. Missing adapters or missing paths are skipped."""
    results = {}
    for agent, path in agent_paths.items():
        try:
            module_path = BUILTIN_ADAPTERS.get(agent)
            if not module_path:
                results[agent] = "skipped: no adapter"
                continue
            mod = importlib.import_module(module_path)
            if not path.exists():
                results[agent] = "skipped: path not found"
                continue
            mod.apply(reg, path)
            results[agent] = "ok"
        except Exception as e:
            results[agent] = f"error: {e}"
    return results
