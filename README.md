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
| `open-free-router setup` | 交互式向导：填写各上游源 API key |
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

# 2. 填入 API key（首次运行推荐）
open-free-router setup

# 3. 启动所有服务（后台运行）
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