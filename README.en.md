# open-free-router

**One command to run everything:**

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh)
open-free-router serve   ← starts proxy(8337) + UI(9057) + scheduler
```

Tracks free models across multiple LLM providers (OpenRouter, NVIDIA NIM, OpenCode Zen, StepFun, SenseNova, Nous Research), runs a local proxy routing by model ID to the correct upstream, and auto-refreshes the model list.

## Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh)
```

## Commands

| Command | Description |
|---|---|
| `open-free-router serve` | **★ One command:** proxy(8337) + UI(9057) + scheduler |
| `open-free-router ui` | Start web dashboard standalone |
| `open-free-router setup` | Interactive wizard: configure API keys |
| `open-free-router refresh [--source NAME] [--dry-run]` | Refresh free models from APIs |
| `open-free-router add NAME --base-url URL [--model ID] [--auto-refresh]` | Add a provider |

## Quick Start

```bash
# 1. Add a provider
open-free-router add openrouter \
  --base-url https://openrouter.ai/api/v1 \
  --api-key sk-or-... \
  --model nvidia/nemotron-3-ultra-550b-a55b:free \
  --auto-refresh

# 2. Add API keys (recommended on first run)
open-free-router setup

# 3. Start all services (background)
open-free-router serve &
```

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
```

## Architecture

| Module | Purpose |
|---|---|
| `proxy.py` | Single-port proxy, model-ID routing to upstream |
| `ui.py` | Web dashboard |
| `serve.py` | Daemon: proxy + UI + scheduler |
| `refresh.py` | Poll provider APIs for free model changes |
| `registry.py` | Registry (Provider/Model data model + persistence) |
| `config.py` | Config loader |

## License

MIT