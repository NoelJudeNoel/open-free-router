# open-free-router

**Free LLM model router, proxy, and sync engine.**

Tracks free models across multiple LLM providers (OpenRouter, NVIDIA NIM, OpenCode Zen, StepFun, SenseNova), runs a local proxy that filters out paid models, and auto-syncs the free model list to all your AI agents.

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│ OpenRouter  │     │ NVIDIA NIM   │ ... │  LLM Provider   │
│ API (free)  │────▶│  API (free)  │────▶│   (upstream)    │
└─────────────┘     └──────────────┘     └─────────────────┘
        ▲                    ▲
        │              refresh-free.py
        │                    │
        │              registry.yaml
        │               (single source of truth)
        │                    │
        │              sync.py ──▶ Hermes / Pi / OMP / OpenCode
        │
  proxy:8337 ──▶ 只暴露 free 模型
  (hermes, opencode 自动 expand 全量列表的克星)
```

## Why

- **Hermes** and **OpenCode** auto-expand the full model catalog from `/v1/models`, cluttering your UI with 300+ paid models
- **Free models change weekly** — new ones appear, old ones disappear
- **Multiple agents need the same list** — Hermes, Pi, OMP, OpenCode, each with different config formats

open-free-router solves all three with a single `registry.yaml` + local proxy + sync engine.

## Install

```bash
git clone https://github.com/YOU/open-free-router.git
cd open-free-router
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Quick Start

```bash
# 1. Add your first provider (OpenRouter)
open-free-router add openrouter \
  --base-url https://openrouter.ai/api/v1 \
  --api-key sk-or-... \
  --model nvidia/nemotron-3-ultra-550b-a55b:free \
  --model google/gemma-4-26b-a4b-it:free \
  --auto-refresh

# 2. Start the proxy (filters paid models for Hermes/OpenCode)
open-free-router proxy

# 3. Start the web dashboard
open-free-router ui

# 4. Sync to all agents
open-free-router sync

# 5. Refresh free model lists (cron this daily)
open-free-router refresh
```

## Commands

| Command | Description |
|---|---|
| `open-free-router proxy` | Start free-model proxy servers (ports 8337/8338) |
| `open-free-router refresh [--source NAME] [--dry-run]` | Refresh free models from APIs |
| `open-free-router sync` | Push registry to all agent configs |
| `open-free-router ui` | Start web dashboard |
| `open-free-router add NAME --base-url URL [--model ID] [--auto-refresh]` | Add a new provider |

## Config

Create `~/.config/open-free-router/config.yaml`:

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

## Registry Format

`registry.yaml` — the single source of truth:

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

## Architecture

| Module | Purpose |
|---|---|
| `config.py` | Config loader, registry path, agent paths |
| `registry.py` | In-memory registry with CRUD + save/load |
| `proxy.py` | Local HTTP proxy filtering non-free models |
| `refresh.py` | Poll provider APIs for new/changed free models |
| `sync.py` | Push registry to Hermes/Pi/OMP/OpenCode configs |
| `ui.py` | Web dashboard (provider status, model list) |

## Supported Agents

| Agent | Config File | Notes |
|---|---|---|
| Hermes | `~/.hermes/config.yaml` | `custom_providers` |
| Pi | `~/.pi/agent/models.json` | `providers.*` |
| OMP | `~/.omp/agent/models.yml` | `providers.*` |
| OpenCode | `~/.config/opencode/opencode.jsonc` | `provider.*` |

## License

MIT
