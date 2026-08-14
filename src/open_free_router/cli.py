#!/usr/bin/env python3
"""open-free-router CLI."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from open_free_router.config import Config
from open_free_router.registry import Registry, ModelInfo, ProviderConfig
from open_free_router.refresh import refresh
from open_free_router.ui import run_ui
from open_free_router.serve import Daemon


TEMPLATE = Path(__file__).parent / "registry.default.yaml"


def _bootstrap_registry(cfg: Config) -> bool:
    """Copy default template to registry path if no registry exists."""
    if cfg.registry_path.exists():
        return False
    if not TEMPLATE.exists():
        return False
    cfg.registry_path.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(TEMPLATE, cfg.registry_path)
    return True


def _ensure_config(cfg: Config):
    """Auto-create config.yaml with defaults if none exists."""
    if cfg.path:
        return
    cfg.path = Path.home() / ".config" / "open-free-router" / "config.yaml"
    cfg.path.parent.mkdir(parents=True, exist_ok=True)
    if not cfg.path.exists():
        import yaml
        cfg.path.write_text(yaml.dump({
            "proxy": {"host": "127.0.0.1", "port": 8337},
            "ui": {"host": "127.0.0.1", "port": 9057},
        }, default_flow_style=False))


def cmd_refresh(args):
    cfg = Config()
    _bootstrap_registry(cfg)
    reg = Registry.load(cfg.registry_path)

    source = args.source
    results = refresh(reg, provider_name=source)

    if source and source not in results:
        print(f"Unknown source: {source}. Available: {sorted(set(results) | {'openrouter','nvidia-nim'})}")
        sys.exit(1)

    changed = any(v for v in results.values())
    if changed and not args.dry_run:
        reg.save(cfg.registry_path, git_history=cfg.registry_git_history)
        print("\n✔ registry updated")
    elif not changed:
        print("\n✓ no changes")


def cmd_ui(args):
    cfg = Config()
    _bootstrap_registry(cfg)
    run_ui(cfg, port=cfg.ui_port)


def cmd_serve(args):
    cfg = Config()
    _ensure_config(cfg)

    bootstrapped = _bootstrap_registry(cfg)
    if bootstrapped:
        n = len(Registry.load(cfg.registry_path).providers)
        print(f"✔ Created {cfg.registry_path} with {n} providers")
        print("  ⚠ No API keys configured yet.")
        print("  Run:  open-free-router setup")
        print()

    cfg.registry_path.parent.mkdir(parents=True, exist_ok=True)
    Daemon(cfg).serve()


def cmd_add(args):
    cfg = Config()
    _bootstrap_registry(cfg)
    reg = Registry.load(cfg.registry_path)

    name = args.name
    base_url = args.base_url
    upstream_url = args.upstream_url or ""
    api_key = args.api_key or ""
    models = [ModelInfo(id=m) for m in (args.models or [])]

    p = ProviderConfig(
        name=name,
        base_url=base_url,
        upstream_url=upstream_url,
        api_key=api_key,
        models=models,
        auto_refresh=args.auto_refresh,
        refresh_method="api" if args.auto_refresh else "manual",
    )
    reg.add_provider(p)
    reg.save(cfg.registry_path, git_history=cfg.registry_git_history)
    print(f"✔ Added provider '{name}' with {len(models)} models")


def cmd_setup(args):
    """Interactive wizard: fill in missing API keys."""
    cfg = Config()
    _ensure_config(cfg)
    bootstrapped = _bootstrap_registry(cfg)

    reg = Registry.load(cfg.registry_path)
    if not reg.providers:
        print("✗ No providers in registry.")
        return

    changed = False
    for name, p in reg.providers.items():

        if p.api_key:
            masked = f"{p.api_key[:8]}...{p.api_key[-4:]}" if len(p.api_key) > 12 else "***"
            print(f"  {name:25s} ✓ {masked}")
            continue

        print(f"  {name:25s} ✗ no key")
        print(f"    upstream: {p.upstream_url or p.base_url}")
        try:
            val = input(f"    Enter API key for {name} (leave blank to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if val:
            p.api_key = val
            changed = True
            print(f"    ✓ key saved")

    if changed:
        reg.save(cfg.registry_path, git_history=cfg.registry_git_history)
        n = sum(1 for p in reg.providers.values() if p.api_key)
        print(f"\n✔ Saved {cfg.registry_path} — {n}/{len(reg.providers)} providers have keys")
        print("  Run  open-free-router serve  to start.")
    else:
        print("\n✓ No changes.")


def cmd_sync(args):
    """Sync registry to agent configs (Pi, OMP, OpenCode, Hermes)."""
    from open_free_router.sync import sync_all
    cfg = Config()
    _bootstrap_registry(cfg)
    reg = Registry.load(cfg.registry_path)

    agents = None
    if args.agent:
        agents = [a.strip() for a in args.agent.split(",")]

    do_write = not args.diff
    results = sync_all(reg, do_write=do_write, agents=agents)

    label = "DIFF" if args.diff else "SYNC"
    print(f"\n=== {label} ===")
    for agent, changes in results.items():
        status = "✔" if do_write else "?"
        print(f"  {status} {agent}: {changes}")

    if do_write:
        print(f"\n✔ Backups at {sync_all.__module__}")
        print("  Restart agents to apply: omp-telegram, opencode, hermes")


def main(args: list[str] | None = None):
    parser = argparse.ArgumentParser(
        prog="open-free-router",
        description="Free LLM model router & sync engine",
    )
    sub = parser.add_subparsers(dest="command")

    p_refresh = sub.add_parser("refresh", help="refresh free model lists from APIs")
    p_refresh.add_argument("--source", help="only refresh this source")
    p_refresh.add_argument("--dry-run", action="store_true")
    p_refresh.set_defaults(func=cmd_refresh)

    p_ui = sub.add_parser("ui", help="start web dashboard")
    p_ui.set_defaults(func=cmd_ui)

    p_serve = sub.add_parser("serve", help="start all services: proxy + UI + scheduler")
    p_serve.set_defaults(func=cmd_serve)

    p_add = sub.add_parser("add", help="add a new provider")
    p_add.add_argument("name")
    p_add.add_argument("--base-url", required=True)
    p_add.add_argument("--upstream-url", default="", help="override upstream URL (defaults to --base-url)")
    p_add.add_argument("--api-key", default="")
    p_add.add_argument("--model", action="append", dest="models")
    p_add.add_argument("--auto-refresh", action="store_true")
    p_add.set_defaults(func=cmd_add)

    p_setup = sub.add_parser("setup", help="interactive wizard: configure API keys for all providers")
    p_setup.set_defaults(func=cmd_setup)

    p_sync = sub.add_parser("sync", help="sync registry to agent configs (Pi, OMP, OpenCode, Hermes)")
    p_sync.add_argument("--agent", help="comma-separated agent names: omp,opencode,hermes")
    p_sync.add_argument("--diff", action="store_true", help="show diff only, don't write")
    p_sync.set_defaults(func=cmd_sync)

    args = parser.parse_args(args)
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
