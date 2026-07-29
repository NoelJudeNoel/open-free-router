"""Sync registry models to agent config files (OMP, OpenCode).

Usage:
    open-free-router sync                  # sync all agents
    open-free-router sync --agent omp      # sync only OMP
    open-free-router sync --agent opencode # sync only OpenCode
    open-free-router sync --diff           # show diff, don't write
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path

from open_free_router.registry import Registry

# ── Paths ──
OMP_MODELS = Path.home() / ".omp" / "agent" / "models.yml"
OMP_CONFIG = Path.home() / ".omp" / "agent" / "config.yml"
OPENCODE_CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"
BACKUP_DIR = Path(f"/root/.openclaw/workspace/agent-backup-{date.today().isoformat()}")


def _backup():
    """Backup agent config files before overwriting."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for f in [OMP_MODELS, OMP_CONFIG, OPENCODE_CONFIG]:
        if f.exists():
            shutil.copy2(str(f), str(BACKUP_DIR / f.name))


def _mask_key(key: str) -> str:
    if not key:
        return "***"
    if len(key) > 12:
        return f"{key[:8]}...{key[-4:]}"
    return "***"


# ══════════════════════════════════════
# OMP sync
# ══════════════════════════════════════
def sync_omp(reg: Registry, do_write: bool = True, proxy_url: str = "http://127.0.0.1:8337/v1") -> list[str]:
    """Sync registry → OMP models.yml."""
    text = OMP_MODELS.read_text() if OMP_MODELS.exists() else "providers:\n"
    changes = []

    for name, p in reg.providers.items():
        key = _mask_key(p.effective_key)
        # Remove existing block
        text = re.sub(rf'^  {re.escape(name)}:\n(?:    [^\n]*\n)*', '', text, flags=re.MULTILINE)
        # Build new block — all providers point to the single-port proxy
        block = f"\n  {name}:\n    baseUrl: {proxy_url}\n    apiKey: {key}\n    api: openai-completions\n    models:\n"
        for m in p.models:
            block += f"      - id: {m.id}\n"
            block += f"        name: {m.name or m.id}\n"
            block += f"        reasoning: {'true' if m.reasoning else 'false'}\n"
            block += f"        input: [text]\n"
            block += f"        contextWindow: {m.context_window}\n"
            block += f"        maxTokens: {m.max_tokens}\n"
            block += f"        cost:\n          input: 0\n          output: 0\n          cacheRead: 0\n          cacheWrite: 0\n"
        text = text.rstrip() + "\n" + block
        changes.append(name)

    text = re.sub(r'\n{3,}', '\n\n', text)
    if do_write:
        if not text.startswith("providers:"):
            text = "providers:\n" + text
        OMP_MODELS.write_text(text)
    return changes


# ══════════════════════════════════════
# OpenCode sync
# ══════════════════════════════════════
def sync_opencode(reg: Registry, do_write: bool = True, proxy_url: str = "http://127.0.0.1:8337/v1") -> list[str]:
    """Sync registry → OpenCode opencode.json."""
    if OPENCODE_CONFIG.exists():
        text = OPENCODE_CONFIG.read_text()
        # Try direct JSON parse first (most OpenCode configs are valid JSON)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Fallback: strip JSONC comments and trailing commas
            text = re.sub(r'(?<![:\"])//[^\\n]*', '', text)
            text = re.sub(r',\s*([}\\]])', r'\\1', text, re.DOTALL)
            data = json.loads(text)
    else:
        data = {"provider": {}}

    if "provider" not in data:
        data["provider"] = {}

    changes = []
    for name, p in reg.providers.items():
        key = _mask_key(p.effective_key)
        models_map = {}
        for m in p.models:
            # OpenCode model key: simplified version of the id
            mkey = m.id.split("/")[-1].replace(":free", "")
            models_map[mkey] = {
                "name": m.name or m.id,
                "id": m.id,
                "reasoning": m.reasoning,
                "limit": {
                    "context": m.context_window,
                    "output": m.max_tokens,
                },
            }
        data["provider"][name] = {
            "name": name.replace("-", " ").title(),
            "npm": "@ai-sdk/openai-compatible",
            "models": models_map,
            "options": {"baseURL": proxy_url, "apiKey": key},
        }
        changes.append(name)

    if do_write:
        OPENCODE_CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return changes


# ══════════════════════════════════════
# Main
# ══════════════════════════════════════
def sync_all(reg: Registry, do_write: bool = True, agents: list[str] | None = None, proxy_url: str = "http://127.0.0.1:8337/v1") -> dict[str, list[str]]:
    """Sync registry to all agents. Returns {agent: [changed_providers]}."""
    if agents is None:
        agents = ["omp", "opencode"]

    if do_write:
        _backup()

    results = {}
    sync_map = {
        "omp": sync_omp,
        "opencode": sync_opencode,
    }

    for agent in agents:
        fn = sync_map.get(agent)
        if fn:
            try:
                changes = fn(reg, do_write=do_write, proxy_url=proxy_url)
                results[agent] = changes
            except Exception as e:
                results[agent] = [f"ERROR: {e}"]

    return results
