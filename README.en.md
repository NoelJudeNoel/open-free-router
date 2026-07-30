# open-free-router

**One command to run everything:** proxy(8337) + UI(9057) + scheduler(12h)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh)
open-free-router serve
```

Tracks free models across 10 LLM providers (OpenRouter, NVIDIA NIM, OpenCode Zen, Nous Research, StepFun, SenseNova, Groq, Google AI Studio, DeepSeek, Poolside AI), runs a local proxy routing by model ID to the correct upstream, and auto-refreshes the model list. Configure once, share across all agents (Hermes, OpenCode, PI, OMP).

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

## Quick Start

```bash
# 1. Auto-creates config on first run, just start
open-free-router serve

# 2. In another terminal, enter API keys
open-free-router setup

# 3. Point all your agents to http://127.0.0.1:8337/v1

# 4. Open dashboard: http://127.0.0.1:9057
```

## Config

`~/.config/open-free-router/config.yaml`:

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

First `serve` auto-creates config + registry from defaults — no manual setup needed.

## Architecture

| Module | Purpose |
|---|---|
| `proxy.py` | Single-port proxy(8337), model-ID routing to upstream. Whitelist-only; unknown models return 403 |
| `serve.py` | Daemon: proxy + UI + scheduler + auto-write Pi models.json |
| `ui.py` | Web dashboard(9057): status, provider CRUD, model refresh, live config editor |
| `refresh.py` | Poll provider APIs for free model changes. Pluggable sources |
| `registry.py` | Registry (ProviderConfig / ModelInfo data model + YAML persistence) |
| `config.py` | Config loader (config.yaml + defaults + path resolution) |
| `cli.py` | CLI entry point (argparse routing) |

### Design Principles

- **Single port 8337** — all agents point to one base_url; routing by model ID
- **Multi-threaded** — ThreadingHTTPServer, no head-of-line blocking
- **User-Agent** — upstream requests set `open-free-router/0.1` to avoid Cloudflare 1010 blocks
- **Zero web framework** — uses stdlib `http.server`, no Flask/FastAPI
- **Pluggable refresh sources** — one module per provider in `refresh_sources/`, exports `fetch(base_url, api_key) → list[ModelInfo]`
- **Auto Pi sync** — writes `~/.pi/agent/models.json` when Pi config dir exists

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

22 tests covering: registry CRUD, config loading, Pi models.json writing, proxy index rebuild.

## License

MIT
