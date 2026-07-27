# open-free-router 架构清理与重构设计

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 清理冗余代码，将 proxy 重构为单端口路由并集成到 `serve` 中，使 `pip install && open-free-router serve` 成为唯一推荐入口。

**架构:** 三合一服务（proxy + UI + scheduler），共享 `registry.yaml` 作为唯一事实源。proxy 从按 provider 分端口改为单端口 8337 + 按 model ID 路由到对应 upstream。Pi 的 models.json 由 serve 内联写入，不再需要 importlib adapter 体系。

**Tech Stack:** Python 3.11+, pyyaml, requests, stdlib http.server

---

## 最终 CLI 形态

```
open-free-router serve     ← ★ 唯一推荐入口：proxy(8337) + UI(9057) + scheduler(12h)
open-free-router ui        ← 单独启动 UI（调试用）
open-free-router refresh   ← 单次拉取免费模型列表
open-free-router add       ← 添加 provider
```

## 架构

```
open-free-router serve
├── proxy (127.0.0.1:8337)
│   ├── GET  /v1/models          → 返回 registry 中所有免费模型
│   └── POST /v1/chat/completions → 按 model ID 路由到对应 upstream
├── UI (127.0.0.1:9057)
│   └── 仪表盘：provider 状态、模型列表、config 编辑
└── scheduler (每 12h)
    ├── refresh → 拉取各 provider 免费模型列表
    └── write_pi_models → 内联写入 ~/.pi/agent/models.json
```

### Proxy 路由逻辑

```
POST /v1/chat/completions { model: "nvidia/nemotron-3-ultra-550b-a55b:free" }
  → 查 model_id→provider 反向索引 → 找到 "openrouter"
  → 转发到 openrouter.upstream_url + openrouter.api_key
  → 返回结果
```

`GET /v1/models` 返回所有 provider 的免费模型合并列表，模型 ID 保持唯一。

### 反向索引

每次 `Registry` 加载时构建 `model_id → provider_name` 映射。请求进来时 O(1) 查找。

### Pi models.json 写入

```python
def write_pi_models(reg: Registry, path: Path | None):
    if not path or not path.parent.exists():
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
    path.write_text(json.dumps(models, indent=2) + "\n")
```

serve 启动时和每次 refresh 后调用，有 Pi 路径就写，没有就跳过。

---

## 清理清单

### 删除文件
- `src/open_free_router/proxy.py` — 重写为单端口版本
- `src/open_free_router/sync.py` — 整个文件
- `src/open_free_router/adapters/` — 整个目录（hermes/pi/omp/opencode 全删）
- `run-ui.sh` — 被 `serve` 替代
- `scripts/new-adapter.sh` — 不再需要
- `src/open_free_router/__init__.py` — 重写 docstring

### 修改文件

| 文件 | 修改内容 |
|------|---------|
| `cli.py` | 删除 `proxy`、`sync`、`new-adapter` 子命令；`serve` 集成 proxy 启动 |
| `config.py` | 删除 `proxy_host`/`proxy_openrouter_port`/`proxy_zen_port`；新增 `proxy_port: int = 8337`；默认 UI 端口改为 9057；删除 `agent_paths` |
| `proxy.py` | **重写**：单端口 handler，`_ProxyHandler` 从 registry 构建反向索引，`GET /v1/models` 合并返回，`POST /v1/chat/completions` 路由转发 |
| `serve.py` | 集成 proxy 启动 + scheduler 中内联 `write_pi_models`；移除所有 adapter/sync 引用 |
| `ui.py` | `/api/status` 中移除 `proxy` 字段 |
| `README.md` | 重写为 `pip install && open-free-router serve` 叙事 |
| `README.en.md` | 同步重写 |
| `AGENTS.md` | 同步更新 |

### 不变
- `refresh.py` / `refresh_sources/` — 不变
- `registry.py` — 不变，但 `_load()` 后需构建反向索引供 proxy 使用
- `config.py` 的 `registry_path` 和 `data_dir` — 不变
- `add` 子命令 — 不变
- 依赖（pyyaml + requests）— 不变

---

## 配置格式变化

```yaml
# 旧
proxy:
  host: 127.0.0.1
  openrouter_port: 8337
  zen_port: 8338

agents:
  hermes: ~/.hermes/config.yaml
  pi: ~/.pi/agent/models.json
  omp: ~/.omp/agent/models.yml
  opencode: ~/.config/opencode/opencode.jsonc

# 新
proxy:
  host: 127.0.0.1
  port: 8337
```

不再有 `agents:` 部分。Pi 的路径如果必要，后续通过环境变量或 cli flag 传入。

---

## 不需要做的事

- 不改 `registry.yaml` 格式
- 不改 refresh source 接口
- 不改依赖（pyyaml + requests 不变）
- 不加新依赖
- 不做无关的代码重构

---

## 验证

1. `open-free-router serve` 启动后，proxy 在 8337 响应 `/v1/models`
2. `open-free-router serve` 启动后，UI 在 9057 可访问
3. 向 8337 发 `POST /v1/chat/completions` 带 registry 中的模型 ID，成功路由到对应 upstream
4. 向 8337 发 `POST /v1/chat/completions` 带非免费模型 ID，返回 403
5. scheduler 到时间后自动执行 refresh + 写入 Pi models.json
6. `open-free-router ui` 独立启动仍可用