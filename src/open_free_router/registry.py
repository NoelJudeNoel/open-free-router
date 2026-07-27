"""Registry CRUD — single source of truth for providers + free models."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelInfo:
    id: str
    name: str = ""
    context_window: int = 131072
    max_tokens: int = 8192
    reasoning: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "ModelInfo":
        return cls(
            id=d["id"],
            name=d.get("name", d["id"]),
            context_window=d.get("context_window", 131072),
            max_tokens=d.get("max_tokens", 8192),
            reasoning=d.get("reasoning", False),
        )

    def to_dict(self) -> dict:
        d = {"id": self.id}
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

    @property
    def effective_key(self) -> str:
        return self.api_keys[0] if self.api_keys else self.api_key

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

    def add_provider(self, cfg: ProviderConfig):
        self.providers[cfg.name] = cfg

    @classmethod
    def load(cls, path: Path) -> "Registry":
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(data)

    def save(self, path: Path):
        import shutil, datetime
        import yaml
        if path.exists():
            backup = path.with_suffix(f".yaml.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}")
            shutil.copy2(path, backup)
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True, sort_keys=False)
