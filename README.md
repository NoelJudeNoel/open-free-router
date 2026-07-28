# open-free-router

**一条命令跑起所有服务：** proxy(8337) + UI(9057) + 定时刷新(12h)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh)
open-free-router serve
```

追踪 9 个 LLM 提供商的免费模型（OpenRouter、NVIDIA NIM、OpenCode Zen、Nous Research、StepFun、SenseNova、Groq、Google AI Studio、DeepSeek），运行本地代理按模型 ID 路由到对应上游，自动刷新模型列表。一次配置，所有 Agent（Hermes、OpenCode、PI、OMP）共享模型。

## 安装

**方式一：一键安装（推荐）**
```bash
bash <(curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh)
```

安装后打开 systemd 开机自启：
```bash
bash <(curl -fsSL ...) --with-systemd
```

**方式二：手动安装**
```bash
git clone https://github.com/NoelJudeNoel/open-free-router.git
cd open-free-router
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 命令

| 命令 | 说明 |
|---|---|
| `open-free-router serve` | **★ 一条命令启动：** proxy(8337) + UI(9057) + 定时刷新(12h) |
| `open-free-router setup` | 交互式向导：填写各上游源 API key |
| `open-free-router refresh [--source NAME] [--dry-run]` | 拉取免费模型列表 |
| `open-free-router add NAME --base-url URL [--model ID] [--auto-refresh]` | 添加 provider |
| `open-free-router ui` | 单独启动 Web 仪表盘（调试用） |

## 快速开始

```bash
# 1. 首次运行自动创建配置，直接启动
open-free-router serve

# 2. 在新终端中填入 API key
open-free-router setup

# 3. 把 Agent 的 base_url 指向 http://127.0.0.1:8337/v1

# 4. 打开仪表盘：http://127.0.0.1:9057
```

## 配置文件

`~/.config/open-free-router/config.yaml`：

```yaml
registry: ~/.config/open-free-router/registry.yaml

proxy:
  host: 127.0.0.1
  port: 8337

ui:
  host: 0.0.0.0
  port: 9057

refresh_interval_hours: 12
```

首次运行 `serve` 自动创建配置文件和注册表，无需手动初始化。

## 架构

| 模块 | 职责 |
|---|---|
| `proxy.py` | 单端口代理(8337)，按模型 ID 路由到对应 upstream。白名单过滤，不在列表的模型返回 403 |
| `serve.py` | 守护进程：拉起 proxy + UI + scheduler，启动时自动写入 Pi models.json |
| `ui.py` | Web 仪表盘（9057）：状态查看、Provider 增删改、模型刷新、实时配置编辑 |
| `refresh.py` | 轮询提供商 API 获取免费模型变化，支持 pluggable sources |
| `registry.py` | 注册中心（ProviderConfig / ModelInfo 数据模型 + YAML 持久化） |
| `config.py` | 配置加载（config.yaml + 默认值 + 路径解析） |
| `cli.py` | CLI 入口（argparse 路由到各子命令） |

### 设计原则

- **单端口 8337** —— 所有 agent 指向同一个 base_url，按模型 ID 路由
- **多线程处理** —— ThreadingHTTPServer，避免单请求阻塞影响其他请求
- **User-Agent 标识** —— 转发时带 `open-free-router/0.1`，避免 Cloudflare 1010 拦截
- **零依赖 Web 框架** —— 使用 Python stdlib `http.server`，无需 Flask/FastAPI
- **刷新源可插拔** —— `refresh_sources/` 下每 provider 一个模块，导出 `fetch(base_url, api_key) → list[ModelInfo]`
- **自动 Pi 同步** —— 检测到 `~/.pi/agent/` 目录存在时自动写入 models.json

## API 端点

| 路径 | 方法 | 说明 |
|---|---|---|
| `/v1/models` | GET | 获取所有免费模型列表（OpenAI 兼容格式） |
| `/v1/chat/completions` | POST | 按模型 ID 路由到上游（OpenAI 兼容格式） |
| `/api/status` | GET | 仪表盘状态 |
| `/api/providers` | GET / POST | Provider 列表 / 增删改 |
| `/api/models` | GET | 按 provider 分组的模型详情 |
| `/api/config` | GET / POST | 配置文件的读取和写入 |
| `/api/refresh` | POST | 手动触发刷新（可指定 --source） |

## 测试

```bash
pip install -e ".[dev]"
python3 -m pytest tests/ -v
```

当前 22 个测试覆盖：registry CRUD、config 加载、server Pi models 写入、proxy index 重建。

## License

MIT
