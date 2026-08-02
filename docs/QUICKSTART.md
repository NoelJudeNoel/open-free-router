# 🆓 open-free-router 快速上手（手把手版）

> 本指南写给**第一次接触这个项目**的用户。不需要懂代理、YAML 或 Python，
> 照着做就行。全程大概 10 分钟。
>
> 如果你已经会用命令行了，只想快点跑起来，看 [README.md](../README.md) 的
> "快速开始"即可；想深入项目内部，看 [AGENTS.md](../AGENTS.md)。

---

## 0. 这个项目是干什么的？我为什么需要它？

你手上可能有几个 AI 编码助手 / 聊天机器人（比如 OpenCode、OMP、Pi、Hermes），
它们每个都要单独的 "API 地址 + API Key" 才能用某个模型，很麻烦。

**open-free-router 帮你做三件事：**

1. **汇聚免费模型** —— 自动跟踪 10 个提供商（OpenRouter、Google、Groq、
   NVIDIA 等）的**免费**模型列表，不用你手动盯着它们更新。
2. **一个地址通吃** —— 在你自己电脑上开一个本地代理（`http://127.0.0.1:8337`），
   你所有 Agent 都指向这一个地址，由它按"模型 ID"自动转发到正确的提供商
   上游。
3. **一次配置，处处生效** —— 填好 API Key 后，可以一键把模型列表同步给
   各个 Agent，不用逐个去改它们的配置。

> 💡 **一句话**：它是你 AI Agent 们的"总机"，负责把免费模型路由到正确的
> 提供商，并且帮你管理"哪个厂商有什么免费模型"这件事。

### 你需要准备的东西

| 需要 | 说明 | 是否必须 |
|---|---|---|
| 一台 Linux 或 macOS 电脑 | Windows 可以用 WSL / Docker，但本指南按 Linux 写 | **必须** |
| Python 3.11+ | 在终端运行 `python3 --version` 检查 | 必须 |
| Git | 一键安装方式会用到 | 推荐 |
| 至少一个免费模型的 API Key | 比如 OpenRouter、Google AI Studio 的 key | 想真正用起来要 |

> 如果你还没有任何 API Key，先把项目装上跑起来（下文），`serve` 和仪表盘
> 不需要 key 也能启动，然后再去注册一个。下面第 5 节教你怎么填 key。

---

## 1. 先确认环境

打开终端，逐条运行并看结果：

```bash
python3 --version
git --version
```

- `python3 --version` 应该显示 `3.11` 或更高（比如 `Python 3.13.5`）。
- `git --version` 应该显示一个 git 版本号（`git version 2.x`）。

如果哪条报"command not found"，先去装对应的东西：

- 没有 Python：`sudo apt install python3 python3-venv python3-pip`（Debian/Ubuntu）
- 没有 git：`sudo apt install git`

> ✅ 到这一步你有：能用的 Python 和 git。

---

## 2. 安装（二选一）

### 方式 A：一键安装（推荐）

在终端粘贴这一行，回车：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh)
```

**这一步做了什么：**

1. 下载仓库代码到 `~/.local/open-free-router/`
2. 在里面建一个独立的 Python 环境（`.venv`，不污染你的系统 Python）
3. 安装这个项目（`pip install -e .`）
4. 如果 `open-free-router` 能写进系统命令目录，会建一个快捷命令

装完你应该看到类似输出：

```
✔ Installation complete
  Install: ~/.local/open-free-router
  Config:  ~/.config/open-free-router/config.yaml
Next steps:
  1. Edit ~/.config/open-free-router/registry.yaml and add your API keys
  2. Or run:  open-free-router setup
  3. Start:   open-free-router serve
```

**验证有没有装上**：新开一个终端（或运行 `source ~/.bashrc`），输入再回车：

```bash
which open-free-router
open-free-router --help
```

- `which open-free-router` 应该打印出命令路径。
- `open-free-router --help` 应该列出可用命令（`serve`、`ui`、`setup`、`refresh`…）。
- 如果提示 `command not found`，可能是命令目录没加入 PATH，见文末 [常见问题 Q1](#q1-装好了但-open-free-router-命令找不到)。

> 想开机自启（Linux + systemd）？用这条：
> ```bash
> bash <(curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh) --with-systemd
> ```
> 这样系统重启后会自动拉起服务。

### 方式 B：手动安装（想自己控制放在哪）

```bash
# 1. 克隆代码
git clone https://github.com/NoelJudeNoel/open-free-router.git
cd open-free-router

# 2. 建独立 Python 环境并激活
python3 -m venv .venv
source .venv/bin/activate

# 3. 安装
pip install -e .
```

装完后，`open-free-router` 这个命令在**当前激活了 venv 的终端**里可用。
（以后每次要用，先 `cd open-free-router && source .venv/bin/activate`。）

---

## 3. 第一次启动

**直接在终端运行：**

```bash
open-free-router serve
```

这是**最重要**的一条命令，它会同时启动三样东西：

1. **代理** —— 监听 `127.0.0.1:8337`，你的 Agent 都连这里
2. **仪表盘** —— 网页管理界面，监听 `127.0.0.1:9057`
3. **定时刷新** —— 每 12 小时自动检查一遍免费模型有没有变化

你第一次运行会看到类似：

```
✔ Created ~/.config/open-free-router/registry.yaml with 10 providers
  ⚠ No API keys configured yet.
  Run:  open-free-router setup

  Proxy  : 127.0.0.1:8337
  UI     : http://127.0.0.1:9057
  Refresh: every 12h
  ...
  🌐 Dashboard: http://0.0.0.0:9057
```

**这个终端要一直开着**（`serve` 是前台运行）。想让它在后台一直跑，见
[第 6 节](#6-让它一直后台运行开机自启)。

**怎么确认它真的成功了？** 另开一个终端，运行：

```bash
curl http://127.0.0.1:8337/v1/models
```

如果返回一大段 JSON，里面有一堆形如 `"id": "or/nemotron-3-ultra:free"` 的
模型条目，说明代理已经在正常提供模型列表了。✅

> ⚠️ 端口被占用会启动失败，报 `Address already in use` 或
> `✗ ... port 8337 ... already in use`。解决办法见 [Q2](#q2-端口被占用启动失败)。


---

## 4. 填入 API Key

现在 `serve` 已经在跑，但还没有任何 key，所以代理虽能列出模型、
**真正调用模型会失败**（因为没有去上游认证的凭据）。

> 你不一定需要填全部 10 个 provider 的 key —— **只填你想用的那几个**就行。
> 不填 key 的 provider 会被跳过，不影响其它 provider 使用。

### 方式一：交互式向导（推荐）

另开一个终端，运行：

```bash
open-free-router setup
```

它会逐个列出 provider，问你是否有 key。**看到有 key 的那个就粘进去，没有就
直接回车跳过**。比如：

```
  openrouter       ✗ no key
    upstream: https://openrouter.ai/api/v1
    Enter API key for openrouter (leave blank to skip): sk-or-v1-xxxxxxxx...(粘贴你的key)
    ✓ key saved
```

填完它会提示保存成功，并统计有几个 provider 已有 key：

```
✔ Saved ~/.config/open-free-router/registry.yaml — 2/10 providers have keys
  Run  open-free-router serve  to start.
```

> 你的 key 存在 `~/.config/open-free-router/registry.yaml` 这个**本地文件**里，
> 是明文。这个文件不要分享、不要提交到 git。见 [安全提示](#7-安全提示)。

### 方式二：手动编辑文件

如果你更喜欢直接改配置文件，用任意文本编辑器打开：

```bash
nano ~/.config/open-free-router/registry.yaml
```

找到你要用的 provider，把 `api_key: ''` 改成你的 key，保存即可：

```yaml
openrouter:
  upstream_url: https://openrouter.ai/api/v1
  api_key: sk-or-v1-你的key放这里   # ← 改成你的 key
  models:
  - id: nemotron-3-ultra:free
  ...
```

改完**重启服务**让改动生效：到运行 `serve` 的终端按 `Ctrl+C`，再重新
`open-free-router serve`。

---

## 5. 用仪表盘看一眼

浏览器打开：

```
http://127.0.0.1:9057
```

你会看到一个网页（Dashboard 仪表盘），有 4 个标签页：

- **Dashboard** —— 显示各 provider 状态、模型数量，有个 "Refresh All Models" 按钮
- **Providers** —— 增删改 provider
- **Config** —— 在线编辑 `config.yaml`
- **Models** —— 按 provider 分组的模型明细

**第一次会弹个 token 输入框**（安全机制）。token 在哪？打开这个文件看：

```bash
cat ~/.config/open-free-router/ui.token
```

把里面的字符串粘贴进去即可（浏览器本次会话记住，不用每次输）。这个 token 只有
本机写操作（保存配置、加 provider、触发刷新）需要，只读浏览不需要。

> 想立刻主动刷新一次模型列表（不用等 12 小时）？在终端运行：
> ```bash
> open-free-router refresh
> ```


---

## 6. 把模型接入你的 Agent

这是最终目的 —— 让你的 AI 助手真正用上这些免费模型。

### 6.1 只需要知道三件事

1. **一个 base_url**：`http://127.0.0.1:8337/v1`
2. **一堆可用的模型 ID**：在 `http://127.0.0.1:8337/v1/models` 里能看到，
   形如 `or/nemotron-3-ultra:free`、`gq/gpt-oss-120b`、`nv/glm-5.2`
3. **一个 API key**：`serve` 默认的代理 key 没有硬性校验（多数 Agent 填个
   占位符即可），如果它必填一项，可以填任意非空字符串。

> 只要你装好这套工具，自己本地测试只需要 base_url 这一条。你的 Agent 只要
> 支持 OpenAI 兼容的 chat API，就都能连上。

### 6.2 用 curl 直接测通（最快确认）

```bash
curl http://127.0.0.1:8337/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "or/nemotron-3-ultra:free",
    "messages": [{"role": "user", "content": "你好，回一句话"}]
  }'
```

返回里有一段 `"choices": [...]` 和内容，就说明**路由成功并真的调用了上游**。✅
（把 `or/nemotron-3-ultra:free` 换成你在 `models` 里看到、且有 key 的模型。）

### 6.3 模型 ID 的几种写法

为兼容不同 Agent，同一个模型可以用多种写法，代理都能识别：

| 写法 | 例子 | 说明 |
|---|---|---|
| 裸 ID | `glm-5.2` | 最简 |
| 前缀/ID | `nv/glm-5.2` | `nv` 是 NVIDIA 的前缀 |
| 上游 ID | `z-ai/glm-5.2` | 厂商全名 |
| 平台/上游 ID | `nvidia-nim/z-ai/glm-5.2` | 某些 Agent 用 |

在 `models` 里直接复制一个 ID 用，准没错。

### 6.4 一键同步到各 Agent（OpenCode / OMP / Pi / Hermes）

如果你装了这些 Agent 的桌面版 / 终端版，且想让它们自动拿到这份模型列表，
跑一条命令：

```bash
open-free-router sync
```

它会把这 10 个 provider、35 个模型写进各个 Agent 的配置文件，并且**先清理
掉旧的、指向本地代理的重复条目**再写，不会越写越多。输出形如：

```
  ✔ pi: [...]
  ✔ omp: [...]
  ✔ opencode: [...]
```

> 改完记得**重启对应的 Agent 进程**让它重新加载配置（sync 输出末尾也会提醒）。
> 同步会先备份旧配置到 `~/.openclaw/agent-backup/`，可以放心。

### 6.5 手动往 OpenCode 里加

如果只想在某一个 Agent 里手动加，以 OpenCode 为例，在它的 provider 配置里加：

```jsonc
"my-free-models": {
  "npm": "@ai-sdk/openai-compatible",
  "options": { "baseURL": "http://127.0.0.1:8337/v1" },
  "models": { "or/nemotron-3-ultra:free": { "name": "Nemotron (free)" } }
}
```

---

## 7. 让它一直后台运行 / 开机自启

`open-free-router serve` 默认在前台（终端关了就停）。想常驻后台：

### 方式 A：nohup（简单，不折腾）

```bash
nohup open-free-router serve > ~/.open-free-router.log 2>&1 &
```

以后想查看日志：`tail -f ~/.open-free-router.log`

### 方式 B：systemd（Linux，开机自启 + 崩溃自动重启，推荐）

**如果是一键安装且带了 `--with-systemd`**，服务已经装好，直接：

```bash
sudo systemctl status open-free-router
sudo systemctl enable --now open-free-router   # 设为开机自启并现在启动
```

**如果是手动安装**，用项目自带的 unit 文件（注意把里面的路径换成你的安装目录）：

```bash
# 假设你的安装目录是 ~/.local/open-free-router
sed "s|/opt/open-free-router|$HOME/.local/open-free-router|g" \
    ~/.local/open-free-router/contrib/systemd/open-free-router.service \
    | sudo tee /etc/systemd/system/open-free-router.service
sudo systemctl daemon-reload
sudo systemctl enable --now open-free-router
```

常用命令：
```bash
sudo systemctl status open-free-router    # 看状态
sudo systemctl restart open-free-router   # 重启
journalctl -u open-free-router -f          # 看实时日志
```

> 注意：同时只能有一个 `serve` 在跑（它自己会检测，重复启动会被拒绝并提示）。
> 别同时跑 `nohup` 那条和 systemd 那条。

---

## 8. 日常操作速查

| 我想… | 命令 |
|---|---|
| 拉最新免费模型 | `open-free-router refresh` |
| 只刷新某一家 | `open-free-router refresh --source openrouter` |
| 预览某次同步会改什么（不真写） | `open-free-router sync --diff` |
| 单独开仪表盘 | `open-free-router ui` |
| 往 registry 加一个 provider | `open-free-router add NAME --base-url URL [--model ID]` |
| 看代理返回了哪些模型 | `curl http://127.0.0.1:8337/v1/models` |
| 检查服务健康 | `curl http://127.0.0.1:8337/` |
| 改刷新间隔（默认 12h） | 编辑 `config.yaml` 的 `refresh_interval_hours` 后重启 |


---

## 9. 常见问题排查

<a name="q1"></a>
### Q1 装好了，但 `open-free-router` 命令找不到

一键安装会把命令软链到 `/usr/local/bin/`。如果仍提示 `command not found`：

```bash
ls -l /usr/local/bin/open-free-router      # 看链接在不在
# 不在的话，手动指定全路径运行：
~/.local/open-free-router/.venv/bin/open-free-router --help
```

或把命令目录加进 PATH（写入 `~/.bashrc` 后 `source ~/.bashrc`）：
```bash
echo 'export PATH="$HOME/.local/open-free-router/.venv/bin:$PATH"' >> ~/.bashrc
```

<a name="q2"></a>
### Q2 端口被占用，启动失败

报错长这样：
```
✗ open-free-router: proxy port 8337 ... already in use.
```
或
```
OSError: [Errno 98] Address already in use
```

说明已经有另一个 `serve`（或别的东西）占了 8337/9057。排查：

```bash
ss -tlnp | grep -E '8337|9057'    # 看谁占了端口
pgrep -af 'open-free-router serve'  # 看是不是已有实例在跑
```

如果有，把它停掉（或 `sudo systemctl stop open-free-router`），再重新 `serve`。
同时只保留一个启动方式（nohup 或 systemd 二选一）。

### Q3 能列出模型，但调用某个模型报 403 / 404

- **403 "not in free whitelist"**：这个模型 ID 不在 registry 里（可能你打错了，
  或它已从免费列表移除）。先 `open-free-router refresh` 更新列表，再在
  `curl .../v1/models` 里看真实存在的 ID。
- **404 "model not found"**：通常是模型 ID 写法/上游 ID 不匹配，换一种写法
  （见 [6.3](#63-模型-id-的几种写法)）或直接复制 models 里的 ID。

### Q4 调用时返回认证/401 错误

绝大多数情况是**该 provider 没填 key** 或 key 填错了。回到第 4 节确认：
`open-free-router setup` 重新填，或检查 `registry.yaml` 里对应 `api_key`。

### Q5 仪表盘保存/添加时提示输入 token，输错被拒

token 是启动时生成的一次性值，看 `~/.config/open-free-router/ui.token`。
如果忘了/想重置，删掉这个文件再重启 `serve` 就会重新生成一个。

### Q6 想完全卸载

```bash
sudo systemctl disable --now open-free-router 2>/dev/null   # 若装了 systemd 服务
sudo rm -f /etc/systemd/system/open-free-router.service
rm -rf ~/.local/open-free-router          # 代码 + venv
rm -rf ~/.config/open-free-router         # 配置 + registry（含 key）
```
> ⚠️ `~/.config/open-free-router` 里有你的 API key，删了不可恢复，确认不再用再删。

### Q7 没看到想用的模型 / 怎样加一个自定义厂商

内置 10 家是"开箱即用"的。如果你有**别家**的支持 OpenAI 兼容 API 的地址，
用 `add` 命令加一个自定义 provider（自定义的不是内置项，`upstream_url` 可自由填）：

```bash
open-free-router add my-company --base-url https://my.api.com/v1 --model my-model --auto-refresh
```

> 注意：`--auto-refresh` 只对内置提供商有效；自定义加的一般手动 `refresh` 即可。

---

## 10. 安全提示（重要，读一遍）

- **API key 是明文存在本机** `~/.config/open-free-router/registry.yaml` 的，
  以及同步后部分 Agent 的配置文件里。**不要**把这些文件提交进 git、不要发截图、
  不要分享给任何人。
- 默认 `proxy.host` / `ui.host` 都是 `127.0.0.1`，**只允许本机访问**。除非你
  非常清楚在做什么，否则**不要**改成 `0.0.0.0` 暴露到局域网/公网——仪表盘的
  写操作即便有 token 鉴权，也没有 HTTPS 加密，不是为公网设计的。
- 内置 provider 的 `upstream_url` 是**固定**的（来自 `registry.default.yaml`），
  即使有 token 也无法通过仪表盘篡改，这是为了防止把真实 key 发去被改写的地址。
- 如果开了 `registry_git_history: true`，会在 `registry.yaml` 旁边生成一个本地
  git 历史（含曾经的 key），同样不要把这个目录分享出去。

---

## 现在就差最后一步

把 6.2 的 curl 命令换成「你的 Agent 的 base_url 指向 `http://127.0.0.1:8337/v1`、
选一个模型 ID」就完成闭环了。祝愉快 🎉

