"""Sync registry models to agent config files (Pi, OMP, OpenCode, Hermes).

Usage:
    open-free-router sync                  # sync all agents
    open-free-router sync --agent omp      # sync only OMP
    open-free-router sync --agent opencode # sync only OpenCode
    open-free-router sync --agent hermes    # sync only Hermes
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
HERMES_CONFIG = Path.home() / ".hermes" / "config.yaml"
PI_MODELS_PATH = Path.home() / ".pi" / "agent" / "models.json"
BACKUP_DIR = Path.home() / ".openclaw" / "agent-backup" / date.today().isoformat()


def write_pi_models(reg: Registry, do_write: bool = True, proxy_url: str = "http://127.0.0.1:8337/v1") -> list[str]:
    """Write registry models to Pi's models.json if Pi config dir exists.

    Pi expects {providers: {name: {baseUrl, models: [...]}}}
    All providers point to the local single-port proxy; routing is by model ID.
    Model IDs are prefixed with provider name (e.g. nv/glm-5.2)
    so users can distinguish which upstream provides the model.

    Always overwrites the entire file — no stale entries can accumulate.
    Returns list of provider names written.
    """
    if not PI_MODELS_PATH.parent.exists():
        return []
    try:
        from open_free_router.config import _PI_PROVIDER_NAMES

        providers = {}
        for name, p in reg.providers.items():
            pi_name = _PI_PROVIDER_NAMES.get(name, name)
            providers[pi_name] = {
                "baseUrl": proxy_url,
                "models": [
                    {
                        "id": f"{p.model_prefix}/{m.id}",
                        "name": m.name or m.id,
                        "contextWindow": m.context_window,
                        "maxTokens": m.max_tokens,
                        "reasoning": m.reasoning,
                    }
                    for m in p.models
                ],
            }
        if do_write:
            data = {"providers": providers}
            PI_MODELS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        total = sum(len(p["models"]) for p in providers.values())
        print(f"  ✓ wrote {total} models to Pi ({PI_MODELS_PATH})")
        return list(providers.keys())
    except Exception as e:
        print(f"  ⚠ failed to write Pi models: {e}")
        return []


def _backup():
    """Backup agent config files before overwriting."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for f in [OMP_MODELS, OMP_CONFIG, OPENCODE_CONFIG, HERMES_CONFIG]:
        if f.exists():
            shutil.copy2(str(f), str(BACKUP_DIR / f.name))


def _mask_key(key: str) -> str:
    """Return the real API key, or a placeholder if none is configured.

    The proxy needs real upstream keys to authenticate with provider APIs.
    Using masked/fake keys would cause all requests to fail with auth errors.
    """
    return key or "sk-no-key"


# ══════════════════════════════════════
# OMP sync
# ══════════════════════════════════════
def sync_omp(reg: Registry, do_write: bool = True, proxy_url: str = "http://127.0.0.1:8337/v1") -> list[str]:
    """Sync registry → OMP models.yml.

    First removes ALL providers that point to the local proxy, then writes
    fresh entries from the registry. Prevents stale duplicates.

    Uses ruamel.yaml's round-trip mode (not regex text surgery) so any
    provider blocks the user configured by hand — comments, formatting,
    unrelated entries — survive untouched. A previous regex-based
    implementation matched "2-space indent, colon at end" to find provider
    blocks; that breaks on anything the regex didn't anticipate (tabs,
    multi-line values, differently-indented comments), silently mangling
    or losing hand-edited config on the same pass that's supposed to only
    touch open-free-router's own entries.
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)

    if OMP_MODELS.exists():
        with open(OMP_MODELS) as f:
            doc = yaml.load(f)
    else:
        doc = None
    if not isinstance(doc, CommentedMap):
        doc = CommentedMap()
    if not isinstance(doc.get("providers"), CommentedMap):
        doc["providers"] = CommentedMap()
    providers = doc["providers"]

    local_proxy_markers = ("127.0.0.1", "localhost")

    def _points_to_local_proxy(block) -> bool:
        if not isinstance(block, dict):
            return False
        return any(marker in str(block.get("baseUrl", "")) for marker in local_proxy_markers)

    # Remove ALL provider blocks pointing to the local proxy (including
    # ones for providers no longer in the registry) so stale entries can't
    # accumulate; unrelated hand-configured providers are left alone.
    removed = [pname for pname in list(providers.keys()) if _points_to_local_proxy(providers[pname])]
    for pname in removed:
        del providers[pname]

    changes = []
    for name, p in reg.providers.items():
        key = _mask_key(p.effective_key)
        block = CommentedMap()
        block["baseUrl"] = proxy_url
        block["apiKey"] = key
        block["api"] = "openai-completions"
        model_blocks = CommentedSeq()
        for m in p.models:
            mblock = CommentedMap()
            mblock["id"] = m.id
            mblock["name"] = m.name or m.id
            mblock["reasoning"] = bool(m.reasoning)
            mblock["input"] = ["text"]
            mblock["contextWindow"] = m.context_window
            mblock["maxTokens"] = m.max_tokens
            mblock["cost"] = CommentedMap(
                [("input", 0), ("output", 0), ("cacheRead", 0), ("cacheWrite", 0)]
            )
            model_blocks.append(mblock)
        block["models"] = model_blocks
        providers[name] = block
        changes.append(name)

    if removed:
        print(f"  Removed {len(removed)} stale OMP providers: {', '.join(removed)}")

    if do_write:
        OMP_MODELS.parent.mkdir(parents=True, exist_ok=True)
        with open(OMP_MODELS, "w") as f:
            yaml.dump(doc, f)
    return changes


# ══════════════════════════════════════
# OpenCode sync
# ══════════════════════════════════════
def sync_opencode(reg: Registry, do_write: bool = True, proxy_url: str = "http://127.0.0.1:8337/v1") -> list[str]:
    """Sync registry → OpenCode opencode.json.

    First removes ALL providers that point to the local proxy (baseURL
    contains 127.0.0.1 or localhost), then writes fresh entries from the
    registry. This prevents duplicate local-* / provider-* entries from
    accumulating across syncs.
    """
    if OPENCODE_CONFIG.exists():
        raw_text = OPENCODE_CONFIG.read_text()
        data = None
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            text = re.sub(r'//[^\n]*', '', raw_text)
            text = re.sub(r',\s*([}\])]', r'\1', text, re.DOTALL)
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                decoder = json.JSONDecoder()
                try:
                    data, _ = decoder.raw_decode(raw_text.lstrip())
                except json.JSONDecodeError:
                    data = {"provider": {}}
    else:
        data = {"provider": {}}

    if "provider" not in data:
        data["provider"] = {}

    # Remove all providers pointing to local proxy (stale duplicates)
    local_proxy_markers = ("127.0.0.1", "localhost")
    removed = []
    for pname in list(data["provider"]):
        opts = data["provider"][pname].get("options", {})
        base_url = opts.get("baseURL", "")
        if any(marker in base_url for marker in local_proxy_markers):
            del data["provider"][pname]
            removed.append(pname)

    # Write fresh entries from registry
    changes = []
    for name, p in reg.providers.items():
        key = _mask_key(p.effective_key)
        models_map = {}
        for m in p.models:
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

    if removed:
        print(f"  Removed {len(removed)} stale local-proxy providers: {', '.join(removed)}")

    if do_write:
        OPENCODE_CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return changes


# ══════════════════════════════════════
# Hermes sync
# ══════════════════════════════════════
def sync_hermes(reg: Registry, do_write: bool = True, proxy_url: str = "http://127.0.0.1:8337/v1") -> list[str]:
    """Ensure Hermes has a custom_providers entry pointing to the proxy.

    Hermes auto-discovers models from the /v1/models endpoint at runtime,
    so we don't need to write a static model list. We only need to ensure
    a custom_providers entry exists with the correct base_url.
    """
    if not HERMES_CONFIG.exists():
        return []

    import yaml

    try:
        text = HERMES_CONFIG.read_text()
        config = yaml.safe_load(text) or {}
    except Exception:
        return []

    if not isinstance(config, dict):
        return []

    custom_providers = config.get("custom_providers", [])
    if not isinstance(custom_providers, list):
        custom_providers = []

    changes = []
    existing_names = {cp.get("name") for cp in custom_providers if isinstance(cp, dict)}

    # Check if we already have an entry pointing to our proxy
    has_entry = any(
        cp.get("base_url", "").rstrip("/") == proxy_url.rstrip("/")
        for cp in custom_providers
        if isinstance(cp, dict)
    )

    if not has_entry:
        # Add a custom_providers entry for the proxy
        # Use the first provider's key as the API key
        key = ""
        for name, p in reg.providers.items():
            if p.effective_key:
                key = p.effective_key
                break

        new_entry = {
            "name": "open-free-router",
            "api_mode": "chat_completions",
            "base_url": proxy_url,
            "api_key": key or "sk-no-key",
            "model": "glm-5.2",  # default model
            "context_length": 262144,
            "max_tokens": 16384,
            "discover_models": True,  # Hermes will auto-discover from /v1/models
        }
        custom_providers.append(new_entry)
        config["custom_providers"] = custom_providers
        changes.append("open-free-router")

    if do_write and changes:
        try:
            # Write back preserving YAML format
            new_text = yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False)
            HERMES_CONFIG.write_text(new_text)
        except Exception:
            pass

    return changes


# ══════════════════════════════════════
# Main
# ══════════════════════════════════════
def sync_all(reg: Registry, do_write: bool = True, agents: list[str] | None = None, proxy_url: str = "http://127.0.0.1:8337/v1") -> dict[str, list[str]]:
    """Sync registry to all agents. Returns {agent: [changed_providers]}."""
    if agents is None:
        agents = ["pi", "omp", "opencode", "hermes"]

    if do_write:
        _backup()

    results = {}
    sync_map = {
        "pi": write_pi_models,
        "omp": sync_omp,
        "opencode": sync_opencode,
        "hermes": sync_hermes,
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
