# open-free-router

**One command to run everything:** proxy(8337) + UI(9057) + scheduler(12h)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh)
open-free-router serve
```

Tracks free models across 7 LLM providers (OpenRouter, NVIDIA NIM, OpenCode Zen, Nous Research, SenseNova, Google AI Studio, Poolside AI), runs a local proxy routing by model ID to the correct upstream, and auto-refreshes the model list. Configure once, share across all agents (Hermes, OpenCode, PI, OMP).

## Install

**Option 1: One-liner (recommended)**
```bash
bash <(curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh)
```

Install with systemd auto-start:
```bash
bash <(curl -fsSL ...) --with-systemd
```

**Option 2: Manual**
```bash
git clone https://github.com/NoelJudeNoel/open-free-router.git
cd open-free-router
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Commands

| Command | Description |
|---|---|
| `open-free-router serve` | **★ One command:** proxy(8337) + UI(9057) + scheduler(12h) |
| `open-free-router setup` | Interactive wizard: fill in API keys for all providers |
| `open-free-router refresh [--source NAME] [--dry-run]` | Refresh free models from APIs |
| `open-free-router add NAME --base-url URL [--model ID] [--auto-refresh]` | Add a provider |
| `open-free-router ui` | Web dashboard standalone (debug) |

## Quick Start (first-time users)

> New here? Read **[the step-by-step walkthrough](docs/QUICKSTART.en.md)** — about 10 minutes,
> from environment checks to connecting your agents, no prior knowledge assumed.
> Shortest path below:

```bash
# 1. Install (one-liner)
bash <(curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh)

# 2. Start everything (proxy:8337 + UI:9057 + scheduler:12h)
open-free-router serve

# 3. In another terminal, enter API keys for the providers you want (Enter to skip)
open-free-router setup

# 4. Verify the proxy returns the model list
curl http://127.0.0.1:8337/v1/models

# 5. Point your agent's base_url at http://127.0.0.1:8337/v1 and pick a model ID

# 6. (Optional) sync the model list to OpenCode / OMP / Pi / Hermes
open-free-router sync
```

Dashboard: <http://127.0.0.1:9057> (the first write action prompts for the token in `~/.config/open-free-router/ui.token`)

**Trouble?** Solutions to common errors (port in use, command not found,
403/404, 401) are in section 9 of the walkthrough.


## Config

`~/.config/open-free-router/config.yaml`:

```yaml
registry: ~/.config/open-free-router/registry.yaml

proxy:
  host: 127.0.0.1
  port: 8337

ui:
  host: 127.0.0.1
  port: 9057

refresh_interval_hours: 12

# Optional, default false. When enabled, every registry.yaml save also
# commits to a local git repo (auto `git init`'d in registry.yaml's
# directory, never pushed anywhere), so `git log -p registry.yaml` shows
# exactly what a given refresh changed and `git revert` can undo it.
# Off by default: registry.yaml holds plaintext API keys, so enabling
# this creates a permanent local history of every key that's ever been
# configured -- a deliberate opt-in, not a default-on behavior.
registry_git_history: false
```

First `serve` auto-creates config + registry from defaults — no manual setup needed.

### Security notes

- `ui.host` and `proxy.host` default to `127.0.0.1` (local only). We do **not** recommend setting either to `0.0.0.0` or exposing them on an untrusted LAN/public network: the dashboard's write endpoints (save config, add/edit providers, trigger refresh) require a local auth token, but the dashboard itself has no HTTPS or fine-grained permissions — it isn't designed for public exposure.
- On first start, the dashboard generates a random token at `<config dir>/ui.token` (mode 0600). The browser will prompt for it once per session when you save config, add a provider, or trigger a refresh. Requests without a valid token get a 401.
- `registry.yaml` stores each provider's API key **in plaintext**. That file, and the per-agent config files it syncs into (`~/.hermes`, `~/.pi`, etc.), should be treated as sensitive — don't commit them or share them. If `registry_git_history` is enabled, the same plaintext history lives in a **local-only** `.git` directory alongside it — treat that the same way.
- For built-in providers (the ones with a `refresh_sources/` module shipped in this repo), `upstream_url` is pinned to the value in `registry.default.yaml`. Even an authenticated `POST /api/providers` with a valid token can't override it — the submitted value is ignored and the response includes `upstream_url_pinned: true`. This closes the path where a valid-but-malicious write could redirect a built-in provider's traffic (and the real API key sent with it) to an attacker's server. Custom providers you add yourself are unaffected and can point at any URL.

## Architecture

| Module | Purpose |
|---|---|
| `proxy.py` | Single-port proxy(8337), model-ID routing to upstream. Whitelist-only; unknown models return 403 |
| `tiers.py` | Maps virtual models `tier/high`\|`mid`\|`low` to an ordered pool of concrete upstream instances |
| `upstream.py` | Actual tier-routing forwarding: retries through the pool in order, auto-switches on failure, short cooldowns |
| `serve.py` | Daemon: proxy + UI + scheduler + auto-write Pi models.json |
| `ui.py` | Web dashboard(9057): status, provider CRUD, model refresh, live config editor |
| `refresh.py` | Poll provider APIs for free model changes. Pluggable sources |
| `registry.py` | Registry (ProviderConfig / ModelInfo data model + YAML persistence) |
| `config.py` | Config loader (config.yaml + defaults + path resolution) |
| `cli.py` | CLI entry point (argparse routing) |

### Design Principles

- **Single port 8337** — all agents point to one base_url; routing by model ID
- **Multi-threaded** — ThreadingHTTPServer, no head-of-line blocking
- **True streaming passthrough** — `stream: true` requests relay upstream SSE line by line, not buffered and returned all at once
- **User-Agent** — upstream requests set `open-free-router/0.1` to avoid Cloudflare 1010 blocks
- **Zero web framework** — uses stdlib `http.server`, no Flask/FastAPI
- **Pluggable refresh sources** — one module per provider in `refresh_sources/`, exports `fetch(base_url, api_key) → list[ModelInfo]`. OpenRouter/Nous/SenseNova auto-detect free models from pricing fields (a structural signal, the most reliable kind); OpenCode Zen detects via a `-free` ID suffix plus a few hardcoded exceptions; NVIDIA NIM/Google AI Studio/Poolside have no pricing field in their upstream API and use a hand-maintained allowlist instead — for these, "auto-refresh" more accurately means "auto-verify the allowlisted IDs still exist," not "auto-discover new free models." The allowlists themselves need periodic manual verification against real API responses by someone holding a key
- **Sync preserves hand-edited config** — agent config files (e.g. OMP's `models.yml`) are edited with `ruamel.yaml` (structured, comment-preserving) rather than text substitution, so only entries pointing at the local proxy are added/removed; any other providers, comments, or formatting the user configured by hand are left untouched
- **Scheduler resilience** — an exception anywhere in a refresh cycle doesn't kill the background thread; it's logged with a full traceback and retried next interval. `/api/status` exposes whether the last cycle failed, so "auto-refresh silently died" is something you can actually notice.
- **Writes gated on real change** — registry backups and every agent's synced config file are only rewritten when `refresh()` finds an actual model-list change, not on every cycle regardless.
- **Built-in providers' upstream_url is pinned** — read from `registry.default.yaml`, not accepted from API/UI submissions, closing the path where a proxy could be redirected to send a real API key to an attacker's server.
- **Auto Pi sync** — writes `~/.pi/agent/models.json` when Pi config dir exists

## Three-tier virtual models (automatic failover)

Besides picking a specific model ID, agents can also target one of
three virtual model names; the proxy tries multiple real instances
within that tier in order and switches automatically on failure.

| Virtual model | Meaning |
|---|---|
| `tier/high` | Flagship-class / million-token context (glm-5.2, deepseek-v4-flash, gemini-3.6-flash, etc.) |
| `tier/mid` | Solid mid-tier: hundreds-of-thousands-token context, or strong coding/reasoning |
| `tier/low` | Catch-all: everything not claimed by high/mid |

Behavior:
- When the same logical model (e.g. `glm-5.2`) is offered by multiple
  providers, instances are tried in order of preference and context
  window size. An instance that fails repeatedly (429/5xx) enters a
  short cooldown and the next one is tried automatically instead of the
  error being surfaced straight to the agent.
- The request's `messages` length is roughly estimated and instances
  whose context window is clearly too small are filtered out before a
  request is even attempted.
- If every instance in a tier fails, the proxy returns 429 with the
  last real upstream status code.
- The preference ordering (`_INSTANCE_PRIORITY` in `tiers.py`) and the
  logical-id lists per tier are currently hand-maintained, not derived
  from `refresh` -- a new free model or a retired one needs to be
  synced into `tiers.py` manually.

## API Endpoints

| Path | Method | Description |
|---|---|---|
| `/v1/models` | GET | List all free models (OpenAI-compatible) |
| `/v1/chat/completions` | POST | Route by model ID to upstream (OpenAI-compatible) |
| `/api/status` | GET | Dashboard status |
| `/api/providers` | GET / POST | Provider list / CRUD |
| `/api/models` | GET | Model details grouped by provider |
| `/api/config` | GET / POST | Read / write config.yaml |
| `/api/refresh` | POST | Trigger model refresh (optional `--source`) |

## Tests

```bash
pip install -e ".[dev]"
python3 -m pytest tests/ -v
```

164 tests covering: tier routing + failover, registry CRUD, config loading, Pi models.json writing, proxy index rebuild, UI auth gating, streaming relay, single-instance guard, allowlist-audit regressions, and more. CI (`.github/workflows/tests.yml`) runs the full suite on Python 3.11 / 3.12.

## License

MIT
