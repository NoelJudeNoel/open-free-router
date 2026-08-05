# 🆓 open-free-router — Step-by-step walkthrough

> Written for **first-time users**. You don't need to know about proxies, YAML,
> or Python — just follow along. About 10 minutes total.
>
> Already comfortable with a terminal and just want the short version? See the
> "Quick Start" in [README.en.md](../README.en.md).

---

## 0. What is this, and why do I need it?

You may have several AI coding assistants / chatbots (e.g. OpenCode, OMP, Pi,
Hermes). Each needs its own "API URL + API key" to use a model — that's tedious.

**open-free-router does three things for you:**

1. **Aggregates free models** — automatically tracks the **free** model lists
   across 7 providers (OpenRouter, Google, NVIDIA, etc.), so you don't
   have to watch for updates yourself.
2. **One URL for everything** — runs a local proxy on your machine
   (`http://127.0.0.1:8337`). All your agents point at this one URL, and it
   routes each request (by **model ID**) to the correct upstream provider.
3. **Configure once, sync everywhere** — after adding API keys you can sync the
   model list to each agent in one command, instead of editing each config.

> 💡 **In one sentence:** it's the "switchboard" for your AI agents — routes
> free models to the right provider and manages "which vendor has which free
> model" for you.

### What you need

| Need | Notes | Required |
|---|---|---|
| A Linux or macOS machine | Windows: use WSL / Docker; this guide is written for Linux | **Yes** |
| Python 3.11+ | Check with `python3 --version` | Yes |
| Git | Used by the one-line installer | Recommended |
| At least one free-model API key | e.g. OpenRouter, Google AI Studio | To actually use it |

> No API key yet? Install and start everything first (below) — `serve` and the
> dashboard run fine without any keys — then get one in section 4.

---

## 1. Check your environment

Open a terminal and run each line, look at the output:

```bash
python3 --version
git --version
```

- `python3 --version` should show `3.11` or higher (e.g. `Python 3.13.5`).
- `git --version` should print a version (e.g. `git version 2.x`).

If either says "command not found", install it first:
- No Python: `sudo apt install python3 python3-venv python3-pip` (Debian/Ubuntu)
- No git: `sudo apt install git`

> ✅ At this point you have working Python and git.

---

## 2. Install (choose one)

### Option A: One-line installer (recommended)

Paste this in your terminal and press Enter:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh)
```

**What this does:**
1. Clones the repo into `~/.local/open-free-router/`
2. Creates a dedicated Python environment (`.venv`, doesn't touch your system Python)
3. Installs the project (`pip install -e .`)
4. Symlinks the `open-free-router` command into the system path if possible

You should see something like:

```
✔ Installation complete
  Install: ~/.local/open-free-router
  Config:  ~/.config/open-free-router/config.yaml
Next steps:
  1. Edit ~/.config/open-free-router/registry.yaml and add your API keys
  2. Or run:  open-free-router setup
  3. Start:   open-free-router serve
```

**Verify it installed** — open a fresh terminal (or `source ~/.bashrc`), run:

```bash
which open-free-router
open-free-router --help
```

- `which open-free-router` prints a path.
- `open-free-router --help` lists the subcommands (`serve`, `ui`, `setup`,
  `refresh`…).
- If it says `command not found`, see [Q1](#q1-the-open-free-router-command-is-not-found).

> Want auto-start on boot (Linux + systemd)? Instead use:
> ```bash
> bash <(curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh) --with-systemd
> ```

### Option B: Manual install (full control)

```bash
git clone https://github.com/NoelJudeNoel/open-free-router.git
cd open-free-router
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

After this, `open-free-router` works **in the terminal where the venv is active**.
(To use it later: `cd open-free-router && source .venv/bin/activate`.)

---

## 3. First launch

**Just run:**

```bash
open-free-router serve
```

This is the most important command. It starts three things at once:
1. **Proxy** — listens on `127.0.0.1:8337`; your agents connect here
2. **Dashboard** — a web UI, listens on `127.0.0.1:9057`
3. **Scheduler** — checks for free-model changes every 12 hours automatically

On first run you'll see something like:

```
✔ Created ~/.config/open-free-router/registry.yaml with 9 providers
  ⚠ No API keys configured yet.
  Run:  open-free-router setup

  Proxy  : 127.0.0.1:8337
  UI     : http://127.0.0.1:9057
  Refresh: every 12h
  ...
  🌐 Dashboard: http://0.0.0.0:9057
```

**Keep this terminal open** (`serve` runs in the foreground). To run it in the
background / on boot, see [section 7](#7-keep-it-running-in-background--on-boot).

**How to confirm it worked?** In a second terminal, run:

```bash
curl http://127.0.0.1:8337/v1/models
```

If you get back a blob of JSON with entries like `"id": "or/nemotron-3-ultra:free"`,
the proxy is serving the model list. ✅

> ⚠️ If the port is taken it fails with `Address already in use` or
> `✗ ... port 8337 ... already in use`. See [Q2](#q2-port-in-use-startup-fails).

---

## 4. Add your API keys

Now `serve` is running, but there are no keys yet — the proxy can list models,
but actual model calls will fail (no credentials to authenticate upstream).

> You don't need keys for all 9 providers. **Fill in only the ones you want to
> use.** Providers without keys are skipped and don't affect the others.

### Option A: Interactive wizard (recommended)

In a second terminal, run:

```bash
open-free-router setup
```

It walks through each provider, asking whether you have a key. **Paste the key
where you have one; press Enter to skip the rest.** Example:

```
  openrouter       ✗ no key
    upstream: https://openrouter.ai/api/v1
    Enter API key for openrouter (leave blank to skip): sk-or-v1-xxxx...(paste your key)
    ✓ key saved
```

It then reports how many providers now have keys:

```
✔ Saved ~/.config/open-free-router/registry.yaml — 2/9 providers have keys
  Run  open-free-router serve  to start.
```

> Your keys are stored **in plaintext** in `~/.config/open-free-router/registry.yaml`
> (a local file). Don't share it or commit it to git. See [Security](#10-security-notes-please-read).

### Option B: Edit the file by hand

Open the file in any text editor:

```bash
nano ~/.config/open-free-router/registry.yaml
```

Find the provider you want and change `api_key: ''` to your key, then save:

```yaml
openrouter:
  upstream_url: https://openrouter.ai/api/v1
  api_key: sk-or-v1-put-your-key-here   # ← your key
  models:
  - id: nemotron-3-ultra:free
  ...
```

**Restart** the service to apply changes: press `Ctrl+C` in the `serve` terminal,
then `open-free-router serve` again.

---

## 5. Look at the dashboard

Open your browser at:

```
http://127.0.0.1:9057
```

A web page (Dashboard) appears with four tabs:
- **Dashboard** — provider status, model counts, a "Refresh All Models" button
- **Providers** — add / edit / remove providers
- **Config** — edit `config.yaml` online
- **Models** — model details grouped by provider

**The first time you do a *write* action it prompts for a token** (a safety
mechanism). Where's the token? Read this file:

```bash
cat ~/.config/open-free-router/ui.token
```

Paste that string in. (The browser remembers it for the session; read-only
browsing doesn't need it.)

> Want to force a refresh now (instead of waiting 12 h)? Run:
> ```bash
> open-free-router refresh
> ```

---

## 6. Connect your agent

This is the end goal — get your AI assistant actually using these free models.

### 6.1 Three things you need to know

1. **One base_url**: `http://127.0.0.1:8337/v1`
2. **A list of model IDs**: visible at `http://127.0.0.1:8337/v1/models`, e.g.
   `or/nemotron-3-ultra:free`, `gq/gpt-oss-120b`, `nv/glm-5.2`
3. **An API key field**: the proxy doesn't enforce a real key locally; if your
   agent requires a non-empty value, any non-empty string works when testing locally.

> If your agent speaks the OpenAI-compatible chat API, it can connect.

### 6.2 Quick end-to-end test with curl

```bash
curl http://127.0.0.1:8337/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "or/nemotron-3-ultra:free",
    "messages": [{"role": "user", "content": "Hello, say something"}]
  }'
```

If the response has a `"choices": [...]` block with content, routing works and
the upstream was really called. ✅ (Swap the model ID for one in `models` that's
free and has a key.)

### 6.3 Model ID formats

The proxy accepts several spellings for the same model:

| Format | Example | Notes |
|---|---|---|
| bare ID | `glm-5.2` | simplest |
| prefix/ID | `nv/glm-5.2` | `nv` = NVIDIA's prefix |
| upstream ID | `z-ai/glm-5.2` | full vendor name |
| provider/upstream ID | `nvidia-nim/z-ai/glm-5.2` | some agents use this |

Just copy an ID from `models` — that always works.

> There are also 3 **virtual models**: `tier/high` (flagship / million-token
> context), `tier/mid` (mid-tier), and `tier/low` (catch-all). When you use
> one of these as `model`, the proxy tries the tier's real upstream
> instances in priority order and fails over automatically. See
> [README "Three-tier virtual models"](../README.en.md#three-tier-virtual-models-automatic-failover).


### 6.4 Sync to agents in one command (OpenCode / OMP / Pi / Hermes)

If you have these installed and want them to pick up the model list, run:

```bash
open-free-router sync
```

It writes the providers/models into each agent's config, **removing stale
local-proxy entries first** so nothing accumulates. Output looks like:

```
  ✔ pi: [...]
  ✔ omp: [...]
  ✔ opencode: [...]
```

> **Restart the agent process** afterward so it reloads the config (the sync
> output reminds you of this too). Sync backs up to `~/.openclaw/agent-backup/`
> first, so it's safe.

### 6.5 Adding to OpenCode manually

To add to a single agent, e.g. OpenCode's provider config:

```jsonc
"my-free-models": {
  "npm": "@ai-sdk/openai-compatible",
  "options": { "baseURL": "http://127.0.0.1:8337/v1" },
  "models": { "or/nemotron-3-ultra:free": { "name": "Nemotron (free)" } }
}
```

---

## 7. Keep it running in the background / on boot

`open-free-router serve` runs in the foreground by default (it stops when you
close the terminal). To keep it running:

### Option A: nohup (simple)

```bash
nohup open-free-router serve > ~/.open-free-router.log 2>&1 &
```

Watch logs: `tail -f ~/.open-free-router.log`

### Option B: systemd (Linux, auto-start + auto-restart on crash; recommended)

**If you installed with `--with-systemd`**, the service is already set up:

```bash
sudo systemctl status open-free-router
sudo systemctl enable --now open-free-router   # enable at boot + start now
```

**If you installed manually**, use the bundled unit file (note the path):

```bash
sed "s|/opt/open-free-router|$HOME/.local/open-free-router|g" \
    ~/.local/open-free-router/contrib/systemd/open-free-router.service \
    | sudo tee /etc/systemd/system/open-free-router.service
sudo systemctl daemon-reload
sudo systemctl enable --now open-free-router
```

Useful commands:
```bash
sudo systemctl status open-free-router    # status
sudo systemctl restart open-free-router   # restart
journalctl -u open-free-router -f          # live logs
```

> Only one `serve` may run at a time (it detects duplicates and refuses to
> start). Don't run the nohup line *and* systemd at the same time.

---

## 8. Day-to-day cheat sheet

| I want to… | Command |
|---|---|
| Pull the latest free models | `open-free-router refresh` |
| Refresh only one provider | `open-free-router refresh --source openrouter` |
| Preview a sync (don't write) | `open-free-router sync --diff` |
| Open just the dashboard | `open-free-router ui` |
| Add a provider to the registry | `open-free-router add NAME --base-url URL [--model ID]` |
| See which models the proxy returns | `curl http://127.0.0.1:8337/v1/models` |
| Health check | `curl http://127.0.0.1:8337/` |
| Change refresh interval (default 12h) | edit `refresh_interval_hours` in `config.yaml`, restart |


---

## 9. Troubleshooting

<a name="q1"></a>
### Q1 The `open-free-router` command is not found

The one-line installer symlinks into `/usr/local/bin/`. If it still says
`command not found`:

```bash
ls -l /usr/local/bin/open-free-router      # is the link there?
# If not, run it by full path:
~/.local/open-free-router/.venv/bin/open-free-router --help
```

or add the dir to PATH (after editing, `source ~/.bashrc`):
```bash
echo 'export PATH="$HOME/.local/open-free-router/.venv/bin:$PATH"' >> ~/.bashrc
```

<a name="q2"></a>
### Q2 Port in use, startup fails

You see:
```
✗ open-free-router: proxy port 8337 ... already in use.
```
or
```
OSError: [Errno 98] Address already in use
```

Another `serve` (or something else) holds 8337/9057. Diagnose:

```bash
ss -tlnp | grep -E '8337|9057'    # what holds the port?
pgrep -af 'open-free-router serve'  # is an instance already running?
```

Stop it (`sudo systemctl stop open-free-router`), then run `serve` again. Also
keep just **one** launch method (nohup *or* systemd, not both).

### Q3 Can list models, but calling one gives 403 / 404

- **403 "not in free whitelist"**: the model ID isn't in the registry (typo, or
  it left the free list). Run `open-free-router refresh` first, then copy a real
  ID from `curl .../v1/models`.
- **404 "model not found"**: usually an ID/upstream-ID mismatch. Try another
  spelling (section 6.3) or copy an ID straight from `models`.

### Q4 Authentication / 401 errors when calling

Almost always the provider has **no key** or a wrong key. Re-check section 4:
`open-free-router setup`, or inspect `api_key` for that provider in
`registry.yaml`.

### Q5 Dashboard asks for a token and rejects it

The token is generated on startup; read it from
`~/.config/open-free-router/ui.token`. To reset it, delete that file and restart
`serve` — a new one is generated.

### Q6 Full uninstall

```bash
sudo systemctl disable --now open-free-router 2>/dev/null   # if systemd was used
sudo rm -f /etc/systemd/system/open-free-router.service
rm -rf ~/.local/open-free-router          # code + venv
rm -rf ~/.config/open-free-router         # config + registry (contains keys)
```
> ⚠️ `~/.config/open-free-router` holds your API keys; deleting it is permanent.
> Only remove it once you're sure you won't use them.

### Q7 Missing model / how to add a custom vendor

The 10 built-in providers work out of the box. If you have **another** vendor
with an OpenAI-compatible API, add a custom provider (`upstream_url` is free-form
for non-built-ins):

```bash
open-free-router add my-company --base-url https://my.api.com/v1 --model my-model --auto-refresh
```

> `--auto-refresh` works for built-in providers; for custom ones, usually just
> run `refresh` manually.

---

## 10. Security notes (please read)

- **API keys are plaintext** in `~/.config/open-free-router/registry.yaml` (and,
  after sync, in some agent config files). **Don't** commit these to git, post
  screenshots, or share them with anyone.
- By default `proxy.host` / `ui.host` are `127.0.0.1` — **local only**. Unless
  you're very sure what you're doing, **don't** change them to `0.0.0.0` and
  expose to a LAN/public network — the dashboard's write endpoints, even with
  token auth, have no HTTPS and aren't designed for public exposure.
- Built-in providers' `upstream_url` is **pinned** (comes from
  `registry.default.yaml`); even a valid token can't change it — this prevents
  the real key from being sent to a rewritten address.
- If you enable `registry_git_history: true`, a local git history (containing
  past keys) is created next to `registry.yaml`. Don't share that directory either.

---

## Just one last step

Turn the curl in 6.2 into "point your agent's base_url at
`http://127.0.0.1:8337/v1`, pick a model ID" and you're done. Enjoy 🎉

