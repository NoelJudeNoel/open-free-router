# 架构清理与重构 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清理冗余代码，将 proxy 重构为单端口路由并集成到 `serve` 中，删除 sync/adapter 体系，使 `pip install && open-free-router serve` 成为唯一推荐入口。

**架构:** 三合一服务（proxy + UI + scheduler），共享 `registry.yaml`。proxy 单端口 8337 按 model ID 路由。Pi models.json 由 serve 内联写入。

**Tech Stack:** Python 3.11+, pyyaml, requests, stdlib http.server

---

## 文件变更总览

| 操作 | 文件 |
|------|------|
| 重写 | `proxy.py` |
| 重写 | `serve.py` |
| 删除 | `sync.py` |
| 删除 | `adapters/` 整个目录 |
| 删除 | `run-ui.sh` |
| 删除 | `scripts/new-adapter.sh` |
| 删除 | `__init__.py`（重写 docstring） |
| 修改 | `cli.py` |
| 修改 | `config.py` |
| 修改 | `ui.py` |
| 修改 | `README.md`、`README.en.md`、`AGENTS.md` |

---

### Task 1: 清理 config.py

**Objective:** 移除 proxy 多端口配置和 agent_paths，改为单端口配置，UI 默认端口改为 9057

**Files:**
- Modify: `src/open_free_router/config.py`

**Step 1: 修改 Config 类**

删除:
- `proxy_host` / `proxy_openrouter_port` / `proxy_zen_port`
- `agent_paths` 字典
- `is_configured` 属性不再需要

新增:
- `proxy_port: int = 8337`

```python
class Config:
    def __init__(self, config_path: Optional[Path] = None):
        self._raw = {}
        if config_path:
            self.path = config_path
        else:
            self.path = self._find_config()
        if self.path and self.path.exists():
            with open(self.path) as f:
                self._raw = yaml.safe_load(f) or {}

        # registry.yaml
        self.registry_path = Path(self._raw.get("registry", "registry.yaml"))
        if not self.registry_path.is_absolute():
            self.registry_path = (self.path.parent if self.path else Path.cwd()) / self.registry_path

        # proxy — single port
        self.proxy_host = self._raw.get("proxy", {}).get("host", "127.0.0.1")
        self.proxy_port = int(self._raw.get("proxy", {}).get("port", 8337))

        # ui
        self.ui_host = self._raw.get("ui", {}).get("host", "127.0.0.1")
        self.ui_port = int(self._raw.get("ui", {}).get("port", 9057))
```

删除 `is_configured` 属性和 `agent_paths` 相关代码。

**Step 2: 验证**

```bash
python3 -c "from open_free_router.config import Config; c = Config(); print(c.proxy_port, c.ui_port)"
# Expected: 8337 9057
```

**Step 3: Commit**

```bash
git add src/open_free_router/config.py
git commit -m "refactor(config): remove multi-port proxy config, agent_paths, default UI to 9057"
```

---

### Task 2: 重写 proxy.py

**Objective:** 单端口 8337 代理，按 model ID 路由到对应 upstream

**Files:**
- Rewrite: `src/open_free_router/proxy.py`

**Step 1: 写入新 proxy.py**

```python
"""Free model proxy — single port, routes by model ID to upstream provider.

GET  /v1/models           → all free models from registry
POST /v1/chat/completions → forward to the correct upstream by model ID
"""
from __future__ import annotations

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import ClassVar
from urllib.request import Request, urlopen
from urllib.error import URLError

from open_free_router.registry import Registry


class _ProxyHandler(BaseHTTPRequestHandler):
    registry: Registry | None = None
    _model_index: ClassVar[dict[str, str]] = {}  # model_id → provider_name
    _index_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def rebuild_index(cls):
        idx = {}
        if cls.registry:
            for name, p in cls.registry.providers.items():
                for m in p.models:
                    idx[m.id] = name
        with cls._index_lock:
            cls._model_index = idx

    def _find_provider(self, model_id: str) -> str | None:
        with self._index_lock:
            return self._model_index.get(model_id)

    def _send_json(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        if path == "/v1/models":
            self._handle_list_models()
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()

        if path == "/v1/chat/completions":
            self._handle_chat_completion(body)
            return
        self._send_json(404, {"error": "not found"})

    def _handle_list_models(self):
        if not self.registry:
            self._send_json(200, {"object": "list", "data": []})
            return
        items = []
        for name, p in self.registry.providers.items():
            for m in p.models:
                items.append({
                    "id": m.id,
                    "object": "model",
                    "created": 0,
                    "owned_by": name,
                })
        self._send_json(200, {"object": "list", "data": items})

    def _handle_chat_completion(self, body: str):
        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid json"})
            return

        model_id = req.get("model", "")
        provider_name = self._find_provider(model_id)
        if not provider_name:
            self._send_json(403, {
                "error": {
                    "message": f"Model '{model_id}' not in free whitelist.",
                    "type": "proxy_error",
                }
            })
            return

        p = self.registry.get(provider_name) if self.registry else None
        if not p or not (p.upstream_url or p.base_url):
            self._send_json(502, {"error": "provider not configured"})
            return

        upstream = (p.upstream_url or p.base_url).rstrip("/")
        key = p.effective_key
        url = f"{upstream}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
        data = body.encode()
        try:
            req_out = Request(url, data=data, headers=headers, method="POST")
            with urlopen(req_out, timeout=120) as r:
                resp = r.read()
                self.send_response(r.status)
                for k, v in r.headers.items():
                    if k.lower() in ("content-type", "content-length"):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp)
        except URLError as e:
            code = getattr(e, "code", 502)
            payload = getattr(e, "read", lambda: b"")() or json.dumps({"error": str(e.reason)}).encode()
            self._send_json(code, json.loads(payload))
        except Exception as e:
            self._send_json(502, {"error": str(e)})

    def log_message(self, format, *args):
        pass


def run_proxy(registry: Registry, host: str = "127.0.0.1", port: int = 8337):
    handler = type("Handler", (_ProxyHandler,), {
        "registry": registry,
    })
    handler.rebuild_index()
    srv = HTTPServer((host, port), handler)
    print(f"  Proxy  : {host}:{port} (single-port, model-ID routing)")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv
```

**Step 2: 验证**

```bash
python3 -c "from open_free_router.proxy import run_proxy; from open_free_router.registry import Registry; r = Registry({'test': {'base_url': 'http://x.com', 'models': [{'id': 'm1'}]}}); run_proxy(r, port=18337); print('ok')"
# Expected: prints "Proxy : 127.0.0.1:18337" and "ok"
```

**Step 3: Commit**

```bash
git add src/open_free_router/proxy.py
git commit -m "refactor(proxy): single-port, model-ID routing, remove multi-port logic"
```

---

### Task 3: 删除 sync.py、adapters/、new-adapter.sh、run-ui.sh

**Objective:** 清理所有不再需要的文件

**Files:**
- Delete: `src/open_free_router/sync.py`
- Delete: `src/open_free_router/adapters/` (整个目录)
- Delete: `scripts/new-adapter.sh`
- Delete: `run-ui.sh`

**Step 1: 删除文件**

```bash
rm src/open_free_router/sync.py
rm -rf src/open_free_router/adapters/
rm scripts/new-adapter.sh
rm run-ui.sh
```

**Step 2: 验证**

```bash
ls src/open_free_router/sync.py 2>&1 || echo "deleted"
ls src/open_free_router/adapters/ 2>&1 || echo "deleted"
# Expected: both say "deleted"
```

**Step 3: Commit**

```bash
git add -A
git commit -m "refactor: remove sync.py, adapters/, new-adapter.sh, run-ui.sh"
```

---

### Task 4: 更新 cli.py

**Objective:** 删除 proxy、sync、new-adapter 子命令，serve 子命令改为启动 proxy + UI + scheduler

**Files:**
- Modify: `src/open_free_router/cli.py`

**Step 1: 修改 cli.py**

```python
#!/usr/bin/env python3
"""open-free-router CLI."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from open_free_router.config import Config
from open_free_router.registry import Registry, ModelInfo, ProviderConfig
from open_free_router.proxy import run_proxy
from open_free_router.refresh import refresh
from open_free_router.ui import run_ui
from open_free_router.serve import Daemon


def cmd_refresh(args):
    cfg = Config()
    reg = Registry.load(cfg.registry_path)
    source = args.source

    results = refresh(reg, provider_name=source)

    if source and source not in results:
        print(f"Unknown source: {source}. Available: {sorted(set(results) | {'openrouter','nvidia-nim'})}")
        sys.exit(1)

    changed = any(v for v in results.values())
    if changed and not args.dry_run:
        reg.save(cfg.registry_path)
        print("\n✔ registry updated")
    elif not changed:
        print("\n✓ no changes")


def cmd_ui(args):
    cfg = Config()
    run_ui(cfg, port=cfg.ui_port)


def cmd_serve(args):
    cfg = Config()
    Daemon(cfg).serve()


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


def main():
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
```

**Step 2: 验证**

```bash
open-free-router --help
# Expected: shows serve, ui, refresh, add (no proxy, no sync)
```

**Step 3: Commit**

```bash
git add src/open_free_router/cli.py
git commit -m "refactor(cli): remove proxy/sync/new-adapter, serve starts proxy+UI+scheduler"
```

---

### Task 5: 重写 serve.py

**Objective:** serve 集成 proxy 启动，scheduler refresh 后内联写入 Pi models.json

**Files:**
- Rewrite: `src/open_free_router/serve.py`

**Step 1: 写入新 serve.py**

```python
#!/usr/bin/env python3
"""open-free-router daemon — proxy + UI + scheduler in one process."""
from __future__ import annotations

import json
import time
import threading
import signal
import sys
from pathlib import Path

from open_free_router.config import Config
from open_free_router.registry import Registry
from open_free_router.proxy import run_proxy
from open_free_router.ui import run_ui
from open_free_router.refresh import refresh


PI_MODELS_PATH = Path.home() / ".pi" / "agent" / "models.json"


def write_pi_models(reg: Registry):
    """Write registry models to Pi's models.json if Pi config dir exists."""
    if not PI_MODELS_PATH.parent.exists():
        return
    models = []
    for p in reg.providers.values():
        for m in p.models:
            models.append({
                "id": m.id,
                "name": m.name or m.id,
                "context_window": m.context_window,
                "max_tokens": m.max_tokens,
                "reasoning": m.reasoning,
            })
    PI_MODELS_PATH.write_text(json.dumps(models, indent=2, ensure_ascii=False) + "\n")
    print(f"  ✓ wrote {len(models)} models to Pi ({PI_MODELS_PATH})")


class Daemon:
    """Runs proxy + UI + scheduler in one process."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.reg = Registry.load(cfg.registry_path)
        self._stop = threading.Event()

    def _scheduler(self):
        interval_hours = 12
        while not self._stop.wait(interval_hours * 3600):
            print("[scheduler] refreshing free models...")
            results = refresh(self.reg)
            if any(results.values()):
                self.reg.save(self.cfg.registry_path)
                print("[scheduler] registry updated")
            write_pi_models(self.reg)

    def serve(self):
        print(f"  Proxy  : {self.cfg.proxy_host}:{self.cfg.proxy_port}")
        print(f"  UI     : http://{self.cfg.ui_host}:{self.cfg.ui_port}")
        print("  Refresh: every 12h")
        print()

        # Start proxy
        run_proxy(self.reg, host=self.cfg.proxy_host, port=self.cfg.proxy_port)

        # Write Pi models on startup
        write_pi_models(self.reg)

        threads = [
            threading.Thread(target=run_ui, args=(self.cfg, self.cfg.ui_port), daemon=True),
            threading.Thread(target=self._scheduler, daemon=True),
        ]

        for t in threads:
            t.start()

        signal.signal(signal.SIGINT, lambda *_: self._stop.set())
        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())

        try:
            while not self._stop.is_set():
                self._stop.wait(1)
        except KeyboardInterrupt:
            pass

        print("\nShutting down...")
        for t in threads:
            t.join(timeout=5)
        print("Done.")
```

**Step 2: 验证**

```bash
python3 -c "from open_free_router.serve import write_pi_models; print('ok')"
# Expected: ok
```

**Step 3: Commit**

```bash
git add src/open_free_router/serve.py
git commit -m "refactor(serve): integrate proxy startup, inline write_pi_models, remove sync"
```

---

### Task 6: 更新 ui.py

**Objective:** 从 `/api/status` 移除 proxy 字段

**Files:**
- Modify: `src/open_free_router/ui.py:62-78`

**Step 1: 修改 `_api_status` 方法**

```python
    def _api_status(self):
        status = {
            "providers": [],
        }
        for name, p in (self.reg.providers if self.reg else {}).items():
            status["providers"].append({
                "name": name,
                "base_url": p.base_url,
                "auto_refresh": p.auto_refresh,
                "model_count": len(p.models),
                "models": [m.id for m in p.models],
            })
        self._send_json(200, status)
```

**Step 2: 验证**

```bash
python3 -c "from open_free_router.ui import _UIHandler; print('ok')"
# Expected: ok
```

**Step 3: Commit**

```bash
git add src/open_free_router/ui.py
git commit -m "refactor(ui): remove proxy status from /api/status"
```

---

### Task 7: 更新 `__init__.py`

**Files:**
- Modify: `src/open_free_router/__init__.py`

**Step 1: 重写 docstring**

```python
"""open-free-router — Free LLM Model Router & Pipeline

Quick start:
    pip install open-free-router
    open-free-router serve   # starts proxy (8337) + UI (9057) + scheduler (12h refresh)
    open-free-router ui      # standalone web dashboard
    open-free-router refresh # one-time free model list refresh
    open-free-router add     # add a provider
"""
```

**Step 2: Commit**

```bash
git add src/open_free_router/__init__.py
git commit -m "docs: update __init__.py docstring for new architecture"
```

---

### Task 8: 更新 README.md 和 README.en.md

**Objective:** 重写为 `pip install && open-free-router serve` 叙事

**Files:**
- Modify: `README.md`
- Modify: `README.en.md`

**Step 1: 重写 README.md**

```markdown
# open-free-router

**一条命令跑起所有服务：**

```bash
pip install open-free-router
open-free-router serve   ← 启动 proxy(8337) + UI(9057) + 定时刷新
```

追踪多个 LLM 提供商（OpenRouter、NVIDIA NIM、OpenCode Zen、StepFun、SenseNova）的免费模型，运行本地代理按模型 ID 路由到对应上游，自动刷新模型列表。

## 安装

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh)
```

## 命令

| 命令 | 说明 |
|---|---|
| `open-free-router serve` | **★ 一键启动：** proxy(8337) + UI(9057) + 定时刷新 |
| `open-free-router ui` | 单独启动 Web 仪表盘 |
| `open-free-router refresh [--source NAME] [--dry-run]` | 拉取免费模型列表 |
| `open-free-router add NAME --base-url URL [--model ID] [--auto-refresh]` | 添加 provider |

## 快速开始

```bash
# 1. 添加 provider
open-free-router add openrouter \
  --base-url https://openrouter.ai/api/v1 \
  --api-key sk-or-... \
  --model nvidia/nemotron-3-ultra-550b-a55b:free \
  --auto-refresh

# 2. 启动所有服务（后台运行）
open-free-router serve &
```

## 配置

`~/.config/open-free-router/config.yaml`：

```yaml
registry: ~/.config/open-free-router/registry.yaml

proxy:
  host: 127.0.0.1
  port: 8337

ui:
  host: 127.0.0.1
  port: 9057
```

## 架构

| 模块 | 职责 |
|---|---|
| `proxy.py` | 单端口代理，按模型 ID 路由到对应 upstream |
| `ui.py` | Web 仪表盘 |
| `serve.py` | 守护进程：proxy + UI + 定时刷新 |
| `refresh.py` | 轮询提供商 API 获取免费模型变化 |
| `registry.py` | 注册中心（Provider/Model 数据模型 + 持久化） |
| `config.py` | 配置加载 |

## License

MIT
```

**Step 2: 同步更新 README.en.md**

**Step 3: Commit**

```bash
git add README.md README.en.md
git commit -m "docs: rewrite README for single-command serve architecture"
```

---

### Task 9: 更新 AGENTS.md

**Files:**
- Modify: `AGENTS.md`

**Step 1: 重写 AGENTS.md**

```markdown
# open-free-router — AGENTS.md

## Entrypoint

- CLI: `open-free-router` (defined in `pyproject.toml` → `open_free_router.cli:main`)
- Install: `pip install -e .` (venv required, Python 3.11+)
- Dependencies: only `pyyaml` + `requests`; no web framework (uses stdlib `http.server`)

## Commands

| Command | What it does |
|---|---|
| `open-free-router serve` | **★ One command:** proxy(8337) + UI(9057) + scheduler (12h refresh) |
| `open-free-router ui` | Web dashboard standalone |
| `open-free-router refresh [--source NAME] [--dry-run]` | Poll provider APIs for free model changes |
| `open-free-router add NAME --base-url URL [--model ID] [--auto-refresh]` | Add a provider to registry |

## Config

- `~/.config/open-free-router/config.yaml` (or `./config.yaml` in CWD)
- `registry.yaml` is the single source of truth for providers + models
- Both files get `.bak-YYYYMMDD-HHMMSS` backups on write
- API keys live in `config.yaml` / `registry.yaml` — never commit

## Architecture

```
src/open_free_router/
├── cli.py        # argparser → routes to serve/ui/refresh/add
├── config.py     # Config class: loads config.yaml, resolves paths
├── registry.py   # Registry CRUD: ProviderConfig + ModelInfo dataclasses
├── proxy.py      # Single-port proxy (8337), routes by model ID to upstream
├── refresh.py    # Dispatches per-provider refresh from refresh_sources/
├── refresh_sources/  # Pluggable: openrouter.py, nvidia_nim.py, etc.
├── serve.py      # Daemon: proxy + UI + scheduler + inline Pi models.json writer
├── ui.py         # Web dashboard
├── templates/    # UI templates
└── web_static/   # UI static assets
```

## Key conventions

- **Proxy uses stdlib `http.server`** — no Flask/FastAPI
- **Refresh sources** are pluggable modules. Each must export `fetch(provider_base_url, api_key) -> list[ModelInfo]`.
- **Pi models.json** is written inline by `serve.py` — no adapter/importlib mechanism
- **Scheduler interval** hardcoded at 12h in `serve.py`
- **ModelInfo serialization** omits default values (context_window=131072, max_tokens=8192, reasoning=False)

## Testing

- `tests/` exists but has no tests yet
- `pytest` available as dev dependency: `pip install -e ".[dev]"`
- No CI/CD configured

## Quirks

- `config.yaml` with relative `registry:` path is resolved relative to config's parent directory, not CWD
```

**Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: update AGENTS.md for cleaned architecture"
```