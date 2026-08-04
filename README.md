# open-free-router

**一条命令跑起所有服务：** proxy(8337) + UI(9057) + 定时刷新(12h)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh)
open-free-router serve
```

追踪 9 个 LLM 提供商的免费模型（OpenRouter、NVIDIA NIM、OpenCode Zen、Nous Research、StepFun、SenseNova、Groq、Google AI Studio、Poolside AI），运行本地代理按模型 ID 路由到对应上游，自动刷新模型列表，当某一LLM api额度用尽时，自动轮替接续不同上游的同一档次模型，设为三档，高档为glm-5.2、deepseek-v4-flash、gemini-3.6-flash。一次配置，所有 Agent（Hermes、OpenCode、PI、OMP）共享模型。

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

## 新手快速上手

> 第一次用？**直接看 《[快速上手手把手教程](docs/QUICKSTART.md)》**，大约 10 分钟，
> 从环境检查到接入你的 Agent 全程无门槛。下面是最短路径：

```bash
# 1. 安装（一键）
bash <(curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh)

# 2. 启动全部服务（proxy:8337 + UI:9057 + 定时刷新:12h）
open-free-router serve

# 3. 另开一个终端，填入你想用的 provider 的 API key（其余回车跳过）
open-free-router setup

# 4. 验证代理正常返回模型列表
curl http://127.0.0.1:8337/v1/models

# 5. 把你的 Agent base_url 指向 http://127.0.0.1:8337/v1，选一个模型 ID

# 6.（可选）一键同步模型列表到 OpenCode / OMP / Pi / Hermes
open-free-router sync
```

浏览器打开仪表盘：<http://127.0.0.1:9057>（首次做写操作会提示输入 `~/.config/open-free-router/ui.token` 里的 token）

**遇到问题？** 常见报错（端口占用、命令找不到、403/404、401）的解决办法都在
《快速上手》第 9 节。


## 配置文件

`~/.config/open-free-router/config.yaml`：

```yaml
registry: ~/.config/open-free-router/registry.yaml

proxy:
  host: 127.0.0.1
  port: 8337

ui:
  host: 127.0.0.1
  port: 9057

refresh_interval_hours: 12

# 可选，默认 false。开启后每次 registry.yaml 保存都会额外提交到一个本地
# git 仓库（自动 git init 在 registry.yaml 所在目录，从不 push 到任何远程），
# 可以用 `git log -p registry.yaml` 看清楚某次刷新具体改了什么、
# `git revert` 回滚。默认关闭：registry.yaml 里是明文 API key，开启这个
# 等于给"每一个用过的 key"建立一份永久本地历史，这是一个值得你主动决定的
# 行为变化，而不是默认帮你做。
registry_git_history: false
```

首次运行 `serve` 自动创建配置文件和注册表，无需手动初始化。

### 安全说明

- `ui.host`、`proxy.host` 默认均为 `127.0.0.1`（仅本机可访问）。**不建议**改成 `0.0.0.0` 或暴露到公网/不受信任的局域网：仪表盘的写操作接口（保存配置、增改 Provider、触发刷新）虽然需要本地 token 鉴权，但仪表盘本身并未做传输加密（无 HTTPS）和更细粒度的权限控制，不是为公网访问设计的。
- 仪表盘首次启动会在 `<config目录>/ui.token` 生成一个随机 token（权限 0600），浏览器打开仪表盘执行"保存配置 / 添加 Provider / 刷新"等操作时会提示输入一次该 token（本次会话内记住）。没有 token 的请求会被拒绝（401）。
- `registry.yaml` 中保存的是各 Provider 的**明文** API key（本地文件，权限跟随系统 umask），该文件以及同步到各 Agent 配置目录（`~/.hermes`、`~/.pi` 等）下的文件都应视为敏感文件，不要提交到版本库或分享给他人。开启 `registry_git_history` 后会多一个**本地**（不会 push）的 `.git` 目录持有同样的明文历史，同样不要把这个目录分享出去或加进公开仓库。
- 对于内置支持的 Provider（`open-free-router` 自带 `refresh_sources/` 模块的那几个），`upstream_url` 会被固定为 `registry.default.yaml` 里的官方地址——即使是携带了正确 token 的 `POST /api/providers` 请求，提交的 `upstream_url` 也会被忽略并强制改回官方值，响应里会带 `upstream_url_pinned: true` 提示。这是为了避免"即使拿到了本地 token，也无法把内置 Provider 的请求重定向到别的地址、进而让代理把真实 key 发过去"。自定义添加的 Provider（不在内置列表里的）不受此限制，可以自由填写任意 `upstream_url`。

## 架构

| 模块 | 职责 |
|---|---|
| `proxy.py` | 单端口代理(8337)，按模型 ID 路由到对应 upstream。白名单过滤，不在列表的模型返回 403 |
| `tiers.py` | 虚拟模型 `tier/high`\|`mid`\|`low` → 一组具体上游实例的映射与排序 |
| `upstream.py` | tier 路由的实际转发：按池顺序重试、失败自动切换下一个实例、短时冷却 |
| `serve.py` | 守护进程：拉起 proxy + UI + scheduler，启动时自动写入 Pi models.json |
| `ui.py` | Web 仪表盘（9057）：状态查看、Provider 增删改、模型刷新、实时配置编辑 |
| `refresh.py` | 轮询提供商 API 获取免费模型变化，支持 pluggable sources |
| `registry.py` | 注册中心（ProviderConfig / ModelInfo 数据模型 + YAML 持久化） |
| `config.py` | 配置加载（config.yaml + 默认值 + 路径解析） |
| `cli.py` | CLI 入口（argparse 路由到各子命令） |

### 设计原则

- **单端口 8337** —— 所有 agent 指向同一个 base_url，按模型 ID 路由
- **多线程处理** —— ThreadingHTTPServer，避免单请求阻塞影响其他请求
- **真流式转发** —— `stream: true` 的请求逐行透传上游 SSE，不会缓冲整个响应后一次性返回
- **User-Agent 标识** —— 转发时带 `open-free-router/0.1`，避免 Cloudflare 1010 拦截
- **零依赖 Web 框架** —— 使用 Python stdlib `http.server`，无需 Flask/FastAPI
- **刷新源可插拔** —— `refresh_sources/` 下每 provider 一个模块，导出 `fetch(base_url, api_key) → list[ModelInfo]`。OpenRouter/Nous/SenseNova 按 pricing 字段自动识别免费模型（结构性信号，最可靠）；OpenCode Zen 按 `-free` ID 后缀 + 少量硬编码例外识别；NVIDIA NIM/Groq/Google AI Studio/StepFun/Poolside 上游 API 不带 pricing 字段，用人工维护的白名单——这几家的"自动刷新"准确说是"自动核对白名单里的 ID 是否还存在"，不是"自动发现新的免费模型"，白名单本身的新鲜度需要持有对应 key 的人定期用真实 API 返回核对
- **同步保留手工配置** —— 写 Agent 配置文件时（如 OMP 的 `models.yml`）用 `ruamel.yaml` 结构化编辑而非文本替换，只增删指向本地代理的条目，其余手工配置的 provider、注释、格式原样保留
- **调度器容错** —— 定时刷新循环内任意一步抛出异常都不会杀死后台线程，会记录完整堆栈到 stderr 并在下一个周期重试；仪表盘能通过 `/api/status` 看到最近一次是否失败，避免"自动刷新静默失效、用户毫无感知"
- **变化才写盘** —— 只有当 `refresh()` 检测到真实的模型列表变化时，才会重写 registry 备份和所有 Agent 的同步文件；一次没有变化的常规刷新不会产生任何多余的磁盘写入
- **已知 Provider 的 upstream_url 被锚定** —— 内置支持的 Provider，其 `upstream_url` 固定读取自 `registry.default.yaml`，不接受 API/UI 提交的覆盖值，从根上避免"代理把真实 key 发到被篡改的地址"这类问题
- **自动 Pi 同步** —— 检测到 `~/.pi/agent/` 目录存在时自动写入 models.json

## 三档虚拟模型（自动故障转移）

除了指定具体模型 ID，Agent 也可以直接用三个虚拟模型名，代理会在同一档位内的多个真实实例之间按顺序尝试、自动切换：

| 虚拟模型 | 说明 |
|---|---|
| `tier/high` | 旗舰级/百万 token 上下文（glm-5.2、deepseek-v4-flash、gemini-3.6-flash 等） |
| `tier/mid` | 中等强度，几十万 token 上下文或强编码/推理能力 |
| `tier/low` | 兜底档：所有未被 high/mid 认领的模型 |

行为：
- 同一个逻辑模型（比如 `glm-5.2`）如果被多个供应商提供，会按"最优先、上下文窗口最大"排序，依次尝试；某个实例连续失败（429/5xx）会进入短时冷却，自动换下一个，而不是直接把错误抛给 Agent。
- 请求体里的 `messages` 长度会做粗略估算，上下文窗口明显不够的实例会被提前过滤掉，不浪费一次请求。
- 整档全部实例都失败时返回 429，带最后一次真实上游状态码。
- 这套优选顺序（`tiers.py` 里的 `_INSTANCE_PRIORITY`）和逻辑模型清单目前是硬编码维护的，不会随 `refresh` 自动更新——供应商加了新模型或某个模型下线，需要手动同步进 `tiers.py`。

### 可观测性

三层，让"这次到底是谁服务的"、"路由整体表现如何"不再是黑盒：

1. **单次请求级别**：非流式的 `tier/*` 请求，响应体里的 `model` 字段会从请求时的别名（`tier/high`）改写成实际服务的实例（`sensenova/glm-5.2`）——跟 OpenAI 自己的 API 在 `gpt-4` 这类别名解析到具体快照版本时的做法一样，任何已经在展示"当前模型：`<字段>`"的客户端不用改代码就能看到真实结果。流式响应**故意不做这个改写**——要做就得对每一行 SSE 都解析+重新序列化，牺牲现在低开销的逐行透传方式，所以流式响应里看到的是上游自己返回的裸模型名（比如 `glm-5.2`），没有供应商前缀，但依然是真实、可用的信息。
2. **聚合状态**：`GET /api/status` 的 `tiers` 字段包含每个 tier 下每个实例的成功/失败次数和当前冷却状态，仪表盘上也有对应的"Tier Routing"展示卡片。
3. **事件日志**：实例进入冷却、整档耗尽时，会往 stdout 打一行 `[tier] ...` 日志（跟调度器的 `[scheduler] ...` 风格一致），不用对着 `/api/status` 的快照猜时间线，日志里直接看得到"发生了什么、为什么切换"。

三层都只在内存里（进程重启或注册表重建时清空），这是"这个进程最近表现如何"，不是持久化的指标存储。

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

当前 164 个测试覆盖：tier 路由与故障转移、registry CRUD、config 加载、Pi models 写入、proxy index 重建、UI 鉴权、流式转发、单实例守护、allowlist 审计回归等。CI（`.github/workflows/tests.yml`）在 Python 3.11 / 3.12 上跑全量。

## License

MIT
