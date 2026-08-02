"""Registry CRUD — single source of truth for providers + free models."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# How many timestamped `*.bak-YYYYMMDD-HHMMSS` backups to keep per file.
# Without this, a long-running `serve` process doing a save on every
# refresh cycle accumulates one backup per cycle forever.
BACKUP_RETENTION = 10


def prune_backups(path: Path, keep: int = BACKUP_RETENTION) -> None:
    """Delete all but the ``keep`` most recent timestamped backups of `path`.

    Backups are named ``<path>.bak-<timestamp>`` (see `Registry.save` /
    `config.save_registry`) and sort correctly by filename since the
    timestamp format is zero-padded and lexicographically ordered.
    """
    pattern = f"{path.name}.bak-*"
    backups = sorted(path.parent.glob(pattern))
    for old in backups[:-keep] if keep > 0 else backups:
        try:
            old.unlink()
        except OSError:
            pass  # best-effort; a failed cleanup shouldn't break the save


def git_commit_registry(path: Path, message: str = "registry.yaml update") -> None:
    """Best-effort: commit the current state of `path` to a git repo
    scoped to its directory, auto-initializing one on first use.

    Layered on top of (not a replacement for) the timestamped .bak-file
    mechanism above -- gives `git log -p` / `git diff` / `git revert`
    for registry.yaml history essentially for free, without hand-rolling
    a second version-control scheme. Opt-in via Config.registry_git_history
    (see config.py for why it defaults off).

    Deliberately never raises: a save must succeed even if git isn't
    installed, the directory can't be made a repo, or anything else
    about this goes wrong. Only ever `git add`s `path` by name, never
    `-A`/`.`, so other files in the same directory (notably ui.token)
    can't end up committed by accident.
    """
    import shutil
    import subprocess

    if shutil.which("git") is None:
        return

    repo_dir = path.parent
    kwargs = dict(cwd=repo_dir, capture_output=True, timeout=10)
    try:
        if not (repo_dir / ".git").exists():
            subprocess.run(["git", "init", "-q"], check=True, **kwargs)
            subprocess.run(["git", "config", "user.email", "open-free-router@localhost"],
                            check=True, **kwargs)
            subprocess.run(["git", "config", "user.name", "open-free-router"], check=True, **kwargs)
            gitignore = repo_dir / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text("# open-free-router: only registry.yaml is meant to be tracked here.\n*\n!registry.yaml\n!.gitignore\n")
                subprocess.run(["git", "add", ".gitignore"], check=True, **kwargs)
                subprocess.run(["git", "commit", "-q", "-m", "open-free-router: init history repo"], **kwargs)
        subprocess.run(["git", "add", path.name], check=True, **kwargs)
        # A no-op commit (content identical to HEAD) exits non-zero; that's
        # expected and not an error, hence no check=True here.
        subprocess.run(["git", "commit", "-q", "-m", message], **kwargs)
    except Exception:
        pass  # best-effort; history tracking must never break a save


@dataclass
class ModelInfo:
    id: str  # Short display ID (e.g. "glm-5.2", "step-3.5-flash")
    upstream_id: str = ""  # Upstream API model ID (e.g. "z-ai/glm-5.2"). Falls back to id.
    name: str = ""
    context_window: int = 131072
    max_tokens: int = 8192
    reasoning: bool = False

    @property
    def effective_upstream_id(self) -> str:
        """Model ID to send to upstream API."""
        return self.upstream_id or self.id

    @classmethod
    def from_dict(cls, d: dict) -> "ModelInfo":
        return cls(
            id=d["id"],
            upstream_id=d.get("upstream_id", ""),
            name=d.get("name", d["id"]),
            context_window=d.get("context_window", 131072),
            max_tokens=d.get("max_tokens", 8192),
            reasoning=d.get("reasoning", False),
        )

    def to_dict(self) -> dict:
        d = {"id": self.id}
        if self.upstream_id:
            d["upstream_id"] = self.upstream_id
        if self.name:
            d["name"] = self.name
        if self.context_window != 131072:
            d["context_window"] = self.context_window
        if self.max_tokens != 8192:
            d["max_tokens"] = self.max_tokens
        if self.reasoning:
            d["reasoning"] = True
        return d


@dataclass
class ProviderConfig:
    name: str
    base_url: str = ""
    upstream_url: str = ""
    api_key: str = ""
    api_keys: list[str] = field(default_factory=list)
    models: list[ModelInfo] = field(default_factory=list)
    auto_refresh: bool = False
    refresh_method: str = "manual"
    prefix: str = ""  # Short prefix for model IDs (e.g. "nv" for nvidia-nim)

    @property
    def effective_key(self) -> str:
        return self.api_keys[0] if self.api_keys else self.api_key

    @property
    def model_prefix(self) -> str:
        """Short prefix for model IDs. Falls back to name if not set."""
        return self.prefix or self.name

    def free_model_ids(self) -> set[str]:
        return {m.id for m in self.models}


class Registry:
    """In-memory registry with save/load."""

    def __init__(self, data: dict | None = None):
        self.providers: dict[str, ProviderConfig] = {}
        if data:
            self._load(data)

    def _load(self, data: dict):
        for name, cfg in data.items():
            if name == "defaults" or not isinstance(cfg, dict):
                continue
            models = [ModelInfo.from_dict(m) for m in cfg.get("models", [])]
            self.providers[name] = ProviderConfig(
                name=name,
                base_url=cfg.get("base_url", ""),
                upstream_url=cfg.get("upstream_url", cfg.get("base_url", "")),
                api_key=cfg.get("api_key", ""),
                api_keys=cfg.get("api_keys", []),
                models=models,
                auto_refresh=cfg.get("auto_refresh", False),
                refresh_method=cfg.get("refresh_method", "manual"),
                prefix=cfg.get("prefix", ""),
            )

    def to_dict(self) -> dict:
        out = {}
        for name, p in self.providers.items():
            d = {
                "base_url": p.base_url,
                "auto_refresh": p.auto_refresh,
                "refresh_method": p.refresh_method,
            }
            if p.upstream_url and p.upstream_url != p.base_url:
                d["upstream_url"] = p.upstream_url
            if p.api_key:
                d["api_key"] = p.api_key
            if p.api_keys:
                d["api_keys"] = p.api_keys
            if p.prefix:
                d["prefix"] = p.prefix
            if p.models:
                d["models"] = [m.to_dict() for m in p.models]
            out[name] = d
        return out

    def get(self, name: str) -> ProviderConfig | None:
        return self.providers.get(name)

    def update_models(self, name: str, models: list[ModelInfo]) -> bool:
        p = self.providers.get(name)
        if not p:
            return False
        p.models = models
        return True

    def add_provider(self, cfg: ProviderConfig) -> bool:
        """Add or replace a provider. Returns True if a submitted
        upstream_url for a known provider was overridden by the pinned
        canonical value (see docstring below for why), so callers like
        ui.py can surface that to whoever made the request instead of
        silently discarding what they typed.

        For providers we have a curated refresh_sources module for (see
        refresh.SOURCE_MAP), upstream_url is pinned to the canonical
        value shipped in registry.default.yaml rather than trusting
        whatever the caller passed in. Phase 0's UI auth token gates
        *who* can call this; this closes *what* they can set for a
        known provider even with valid auth -- upstream_url simply
        isn't attacker-influenceable input for those names anymore,
        rather than merely being harder to reach. Custom/user-added
        providers (not in SOURCE_MAP) keep full freedom to set any
        upstream_url, since that's the whole point of being able to
        add a provider by hand.

        Import is deferred (not at module top) to avoid a circular
        import: refresh.py imports ModelInfo from this module.
        """
        from open_free_router.refresh import SOURCE_MAP, CANONICAL_UPSTREAM_URLS
        pinned = False
        if cfg.name in SOURCE_MAP:
            canonical = CANONICAL_UPSTREAM_URLS.get(cfg.name)
            if canonical and cfg.upstream_url != canonical:
                print(f"  ⚠ ignoring submitted upstream_url for known provider "
                      f"'{cfg.name}' ({cfg.upstream_url!r}); pinned to {canonical!r}")
                cfg.upstream_url = canonical
                pinned = True
        self.providers[cfg.name] = cfg
        return pinned

    @classmethod
    def load(cls, path: Path) -> "Registry":
        import yaml
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            data = {}
        return cls(data)

    def save(self, path: Path, git_history: bool = False):
        import shutil, datetime
        import yaml
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = path.with_suffix(f".yaml.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}")
            shutil.copy2(path, backup)
            prune_backups(path)
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        if git_history:
            git_commit_registry(path)
