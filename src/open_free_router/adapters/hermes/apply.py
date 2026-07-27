#!/usr/bin/env python3
"""Hermes adapter: sync registry to ~/.hermes/config.yaml."""
from __future__ import annotations

import yaml
from pathlib import Path

from open_free_router.registry import Registry


def apply(reg: Registry, path: Path):
    """Merge registry providers into Hermes custom_providers."""
    with open(path) as f:
        config = yaml.safe_load(f) or {}

    existing = {cp["name"]: cp for cp in config.get("custom_providers", [])}
    for name, p in reg.providers.items():
        if not p.base_url:
            continue
        entry = {
            "name": name,
            "base_url": p.base_url,
            "api_key": p.effective_key,
            "model": p.models[0].id if p.models else "",
        }
        if p.models:
            entry["models"] = [m.to_dict() for m in p.models]
        existing[name] = entry

    config["custom_providers"] = list(existing.values())
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
