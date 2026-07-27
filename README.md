# open-free-router

**Free LLM 模型路由、代理与同步引擎。**

追踪多个 LLM 提供商（OpenRouter、NVIDIA NIM、OpenCode Zen、StepFun、SenseNova）的免费模型，运行本地代理过滤付费模型，并自动将免费模型列表同步到你所有的 AI 助手。

[English README →](README.en.md)

## 为什么需要这个

- **Hermes** 和 **OpenCode** 会从 `/v1/models` 自动展开完整模型目录，UI 里堆 300+ 付费模型
- **免费模型每周都在变** — 新的出现，旧的消失
- **多个助手需要同一份列表** — Hermes、Pi、OMP、OpenCode，各自配置格式不同

open-free-router 用一份 `registry.yaml` + 本地代理 + 同步引擎解决所有问题。

## 核心设计

```
┌──────────────────────────────────────────────────────┐
│                   open-free-router                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│  │  Registry   │  │   Proxy     │  │   Refresh   │   │
│  │  (YAML)     │  │  :8337/8338 │  │   (cron)    │   │
│  └──────┬──────┘  └──────┬──────┘  └─────────────┘   │
│         │                │                            │
│    sync.py          ANY LLM CLIENT                     │
│    (adapters)       (OpenAI-compatible)                │
│    ↓                ↓                                 │
│  Hermes/Pi/OMP/    curl / OpenAI SDK /                │
│  OpenCode config   任何支持 OpenAI API 的客户端        │
└──────────────────────────────────────────────────────┘
```

**proxy 是核心接口。** 任何 OpenAI-compatible client 都能直接用，不需要 sync。

## 安装

```bash
# 一行命令安装
bash <(curl -fsSL https://raw.githubusercontent.com/YOU/open-free-router/main/scripts/install.sh)

# 或手动安装
git clone https://github.com/YOU/open-free-router.git ~/.local/open-free-router
cd ~/.local/open-free-router
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## 快速开始

```bash
# 1. 添加 provider（OpenRouter）
open-free-router add openrouter \
  --base-url https://openrouter.ai/api/v1 \
  --api-key sk-or-... \
  --model nvidia/nemotron-3-ultra-550b-a55b:free \
  --auto-refresh

# 2. 启动代理（为 Hermes/OpenCode 过滤付费模型）
open-free-router proxy

# 3. 启动 web 控制台
open-free-router ui

# 4. 同步到所有 agent
open-free-router sync

# 5. 刷新免费模型列表（加入 cron 每日执行）
open-free-router refresh
```

## 命令

| 命令 | 说明 |
|---|---|
| `open-free-router proxy` | 启动免费模型代理（端口 8337/8338） |
| `open-free-router refresh [--source NAME] [--dry-run]` | 从 API 刷新免费模型列表 |
| `open-free-router sync` | 推送 registry 到所有 agent 配置 |
| `open-free-router ui` | 启动 web 控制台 |
| `open-free-router add NAME --base-url URL [--model ID] [--auto-refresh]` | 添加新 provider |
| `open-free-router new-adapter <name>` | 生成新 agent adapter 模板 |

## 配置

创建 `~/.config/open-free-router/config.yaml`：

```yaml
registry: ~/.config/open-free-router/registry.yaml

proxy:
  host: 127.0.0.1
  openrouter_port: 8337
  zen_port: 8338

ui:
  host: 127.0.0.1
  port: 9527

agents:
  hermes: ~/.hermes/config.yaml
  pi: ~/.pi/agent/models.json
  omp: ~/.omp/agent/models.yml
  opencode: ~/.config/opencode/opencode.jsonc
```

## Registry 格式

`registry.yaml` — 唯一事实源：

```yaml
openrouter:
  base_url: http://127.0.0.1:8337/v1
  upstream_url: https://openrouter.ai/api/v1
  api_key: sk-or-...
  auto_refresh: true
  refresh_method: openrouter_api
  models:
    - id: nvidia/nemotron-3-ultra-550b-a55b:free
      name: Nemotron 3 Ultra
      context_window: 1000000
      max_tokens: 16384
      reasoning: true

nvidia-nim:
  base_url: https://integrate.api.nvidia.com/v1
  api_key: nvapi-...
  auto_refresh: false
  models:
    - id: z-ai/glm-5.2
    - id: stepfun-ai/step-3.7-flash
```

## 架构

| 模块 | 职责 |
|---|---|
| `config.py` | 配置加载（config.yaml、registry 路径、agent 路径） |
| `registry.py` | 注册中心（Provider/Model dataclass + CRUD + 持久化） |
| `proxy.py` | 本地 HTTP 代理，过滤非免费模型 |
| `refresh.py` | 轮询提供商 API 获取免费模型变化（OpenRouter + NIM） |
| `sync.py` | Adapter 调度器，按 agent 类型分发到对应 adapter |
| `ui.py` | Web 控制台（provider 状态、模型列表、config 编辑器） |
| `adapters/*/apply.py` | 各 agent 配置格式转换（可插拔） |

## 支持的 Agent

| Agent | 配置文件 | Adapter |
|---|---|---|
| Hermes | `~/.hermes/config.yaml` | `adapters/hermes` |
| Pi | `~/.pi/agent/models.json` | `adapters/pi` |
| OMP | `~/.omp/agent/models.yml` | `adapters/omp` |
| OpenCode | `~/.config/opencode/opencode.jsonc` | `adapters/opencode` |

## 添加新 Agent

```bash
# 1. 生成 adapter 模板
open-free-router new-adapter myagent

# 2. 编辑 adapters/myagent/apply.py，实现配置格式转换

# 3. 在 config.yaml 里添加路径
agents:
  myagent: /path/to/myagent/config
```

## 通用接口（不需要 sync）

任何 OpenAI-compatible client 直接连 proxy：

```bash
curl http://127.0.0.1:8337/v1/chat/completions \
  -H "Authorization: Bearer any-key" \
  -d '{"model":"nvidia/nemotron-3-ultra-550b-a55b:free","messages":[{"role":"user","content":"hi"}]}'
```

## 运行环境

| 环境 | 支持 |
|---|---|
| Linux / macOS / Windows | ✅ 纯 Python 3.11+ |
| Docker | ✅ 映射 8337/8338/9527 |
| ARM (树莓派 / Oracle ARM) | ✅ 无原生依赖 |

## License

MIT
