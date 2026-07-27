#!/usr/bin/env python3
"""OMP adapter: sync registry to ~/.omp/agent/models.yml."""
from __future__ import annotations

from pathlib import Path

from open_free_router.registry import Registry


def apply(reg: Registry, path: Path):
    """Rewrite OMP models.yml from registry."""
    lines = ["providers:\n"]
    for name, p in reg.providers.items():
        if not p.base_url:
            continue
        lines.append(f"\n  {name}:\n")
        lines.append(f"    baseUrl: {p.base_url}\n")
        lines.append(f'    apiKey: "{p.effective_key}"\n')
        lines.append("    api: openai-completions\n")
        lines.append("    models:\n")
        for m in p.models:
            lines.append(f"      - id: {m.id}\n")
            lines.append(f'        name: "{m.name}"\n')
            lines.append(f"        reasoning: {'true' if m.reasoning else 'false'}\n")
            lines.append("        input: [text]\n")
            lines.append(f"        contextWindow: {m.context_window}\n")
            lines.append(f"        maxTokens: {m.max_tokens}\n")
            lines.append("        cost:\n")
            lines.append("          input: 0\n          output: 0\n")
            lines.append("          cacheRead: 0\n          cacheWrite: 0\n")
    path.write_text("".join(lines))
