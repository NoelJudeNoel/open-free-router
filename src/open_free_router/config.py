"""Configuration loader for open-free-router."""
import os
from pathlib import Path
from typing import Optional

import yaml


DEFAULT_CONFIG_PATHS = [
    Path.home() / ".config" / "open-free-router" / "config.yaml",
    Path.cwd() / "config.yaml",
]


class Config:
    """Central config: registry path, proxy host/port, UI host/port."""

    def __init__(self, config_path: Optional[Path] = None):
        self._raw = {}
        if config_path:
            self.path = config_path
        else:
            self.path = self._find_config()
        if self.path and self.path.exists():
            with open(self.path) as f:
                self._raw = yaml.safe_load(f) or {}

        # registry.yaml (single source of truth for providers + models)
        self.registry_path = Path(self._raw.get("registry", "registry.yaml"))
        if not self.registry_path.is_absolute():
            base = self.path.parent if self.path else Path.home() / ".config" / "open-free-router"
            self.registry_path = base / self.registry_path

        # proxy
        self.proxy_host = self._raw.get("proxy", {}).get("host", "127.0.0.1")
        self.proxy_port = int(self._raw.get("proxy", {}).get("port", 8337))

        # ui
        self.ui_host = self._raw.get("ui", {}).get("host", "127.0.0.1")
        self.ui_port = int(self._raw.get("ui", {}).get("port", 9057))

    @staticmethod
    def _find_config() -> Optional[Path]:
        for p in DEFAULT_CONFIG_PATHS:
            if p.exists():
                return p
        return None

    @property
    def data_dir(self) -> Path:
        """Directory for logs, backups, etc."""
        d = Path(self._raw.get("data_dir", Path.home() / ".local" / "share" / "open-free-router"))
        d.mkdir(parents=True, exist_ok=True)
        return d


def load_registry(registry_path: Path):
    """Load registry.yaml and return dict."""
    import yaml
    with open(registry_path) as f:
        return yaml.safe_load(f) or {}


def save_registry(registry_path: Path, data: dict):
    """Save registry.yaml with backup."""
    import shutil, datetime
    if registry_path.exists():
        backup = registry_path.with_suffix(
            f".yaml.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}"
        )
        shutil.copy2(registry_path, backup)
    with open(registry_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
