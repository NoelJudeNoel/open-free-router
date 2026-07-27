#!/usr/bin/env python3
"""OpenCode adapter: sync registry to ~/.config/opencode/opencode.jsonc."""
from __future__ import annotations

import json
from pathlib import Path

from open_free_router.registry import Registry


def apply(reg: Registry, path: Path):
    """Merge registry providers into OpenCode provider map."""
    try:
        import json5
        data = json5.loads(path.read_text())
    except Exception:
        # fallback: best-effort JSON parse
        data = json.loads(path.read_text())

    for name, p in reg.providers.items():
        if not p.base_url:
            continue
        data.setdefault("provider", {})[name] = {
            "options": {
                "baseURL": p.base_url,
                "apiKey": p.effective_key,
            },
            "models": {m.id: {"name": m.name} for m in p.models},
        }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
