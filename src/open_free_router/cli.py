#!/usr/bin/env python3
"""open-free-router CLI."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from open_free_router.config import Config
from open_free_router.registry import Registry, ModelInfo, ProviderConfig
from open_free_router.proxy import run_proxy
from open_free_router.refresh import refresh_openrouter, refresh_nvidia_nim
from open_free_router.sync import sync_all
from open_free_router.ui import run_ui


def cmd_proxy(args):
    cfg = Config()
    reg = Registry.load(cfg.registry_path)
    run_proxy(reg, host=cfg.proxy_host,
              openrouter_port=cfg.proxy_openrouter_port,
              zen_port=cfg.proxy_zen_port)


def cmd_refresh(args):
    cfg = Config()
    reg = Registry.load(cfg.registry_path)
    source = args.source

    refreshers = {
        "openrouter": refresh_openrouter,
        "nvidia-nim": refresh_nvidia_nim,
    }

    if source:
        if source not in refreshers:
            print(f"Unknown source: {source}. Available: {list(refreshers)}")
            sys.exit(1)
        results = {source: refreshers[source](reg)}
    else:
        results = {}
        for name, fn in refreshers.items():
            results[name] = fn(reg)

    changed = any(r is not None for r in results.values())
    if changed and not args.dry_run:
        reg.save(cfg.registry_path)
        print("\n✔ registry updated — run 'open-free-router sync' to apply")
    elif not changed:
        print("\n✓ no changes")


def cmd_sync(args):
    cfg = Config()
    reg = Registry.load(cfg.registry_path)
    sync_all(reg, cfg.agent_paths)


def cmd_ui(args):
    cfg = Config()
    run_ui(cfg, port=cfg.ui_port)


def cmd_add(args):
    cfg = Config()
    reg = Registry.load(cfg.registry_path)

    name = args.name
    base_url = args.base_url
    api_key = args.api_key or ""
    models = [ModelInfo(id=m) for m in (args.models or [])]

    p = ProviderConfig(
        name=name,
        base_url=base_url,
        api_key=api_key,
        models=models,
        auto_refresh=args.auto_refresh,
        refresh_method="api" if args.auto_refresh else "manual",
    )
    reg.add_provider(p)
    reg.save(cfg.registry_path)
    print(f"✔ Added provider '{name}' with {len(models)} models")
    print(f"  Run: open-free-router sync")


def main():
    parser = argparse.ArgumentParser(
        prog="open-free-router",
        description="Free LLM model router & sync engine",
    )
    sub = parser.add_subparsers(dest="command")

    p_proxy = sub.add_parser("proxy", help="start free-model proxy servers")
    p_proxy.set_defaults(func=cmd_proxy)

    p_refresh = sub.add_parser("refresh", help="refresh free model lists from APIs")
    p_refresh.add_argument("--source", help="only refresh this source")
    p_refresh.add_argument("--dry-run", action="store_true")
    p_refresh.set_defaults(func=cmd_refresh)

    p_sync = sub.add_parser("sync", help="sync registry to all agent configs")
    p_sync.set_defaults(func=cmd_sync)

    p_ui = sub.add_parser("ui", help="start web dashboard")
    p_ui.set_defaults(func=cmd_ui)

    p_add = sub.add_parser("add", help="add a new provider")
    p_add.add_argument("name")
    p_add.add_argument("--base-url", required=True)
    p_add.add_argument("--api-key", default="")
    p_add.add_argument("--model", action="append", dest="models")
    p_add.add_argument("--auto-refresh", action="store_true")
    p_add.set_defaults(func=cmd_add)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
