#!/usr/bin/env python3
"""Pi adapter: sync registry to ~/.pi/agent/models.json."""
from __future__ import annotations

import json
from pathlib import Path

from open_free_router.registry import Registry


def apply(reg: Registry, path: Path):
    """Merge registry providers into Pi models.json."""
    data = json.loads(path.read_text())
    for name, p in reg.providers.items():
        if not p.base_url:
            continue
        data.setdefault("providers", {})[name] = {
            "baseUrl": p.base_url,
            "apiKey": p.effective_key,
            "models": [m.to_dict() for m in p.models],
        }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
