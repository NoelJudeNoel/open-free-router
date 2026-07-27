# open-free-router — AGENTS.md

## Entrypoint

- CLI: `open-free-router` (defined in `pyproject.toml` → `open_free_router.cli:main`)
- Install: `pip install -e .` (venv required, Python 3.11+)
- Dependencies: only `pyyaml` + `requests`; no web framework (uses stdlib `http.server`)

## Commands

| Command | What it does |
|---|---|
| `open-free-router serve` | **★ One command:** proxy(8337) + UI(9057) + scheduler (12h refresh) |
| `open-free-router ui` | Web dashboard standalone |
| `open-free-router setup` | Interactive wizard: fill in API keys for all providers |
| `open-free-router refresh [--source NAME] [--dry-run]` | Poll provider APIs for free model changes |
| `open-free-router add NAME --base-url URL [--upstream-url URL] [--model ID] [--auto-refresh]` | Add a provider to registry |

## Config

- `~/.config/open-free-router/config.yaml` (or `./config.yaml` in CWD)
- `registry.yaml` is the single source of truth for providers + models
- Both files get `.bak-YYYYMMDD-HHMMSS` backups on write
- API keys live in `registry.yaml` — never commit
- On first run with no config: `serve` auto-creates `config.yaml` + copies `registry.default.yaml` to `registry.yaml` (no API keys)

## Bootstrap

- `src/open_free_router/registry.default.yaml` — template with 9 upstream sources and model lists, no API keys
- `scripts/install.sh` — one-liner installer (git clone + venv + setup)
- First `serve` auto-bootstraps; `open-free-router setup` walks through key entry interactively

## Architecture

## Config

- `~/.config/open-free-router/config.yaml` (or `./config.yaml` in CWD)
- `registry.yaml` is the single source of truth for providers + models
- Both files get `.bak-YYYYMMDD-HHMMSS` backups on write
- API keys live in `config.yaml` / `registry.yaml` — never commit

## Architecture

```
src/open_free_router/
├── cli.py        # argparser → routes to serve/ui/refresh/add/setup
├── config.py     # Config class: loads config.yaml, resolves paths
├── registry.py   # Registry CRUD: ProviderConfig + ModelInfo dataclasses
├── registry.default.yaml  # Template with 8 upstream sources, no API keys
├── proxy.py      # Single-port proxy (8337), routes by model ID to upstream
├── refresh.py    # Dispatches per-provider refresh from refresh_sources/
├── refresh_sources/  # Pluggable: openrouter.py, nvidia_nim.py, etc.
├── serve.py      # Daemon: proxy + UI + scheduler + inline Pi models.json writer
├── ui.py         # Web dashboard
├── templates/    # UI templates
└── web_static/   # UI static assets
```

## Key conventions

- **Proxy uses stdlib `http.server`** — no Flask/FastAPI
- **Refresh sources** are pluggable modules. Each must export `fetch(provider_base_url, api_key) -> list[ModelInfo]`.
- **Pi models.json** is written inline by `serve.py` — no adapter/importlib mechanism
- **Scheduler interval** hardcoded at 12h in `serve.py`
- **ModelInfo serialization** omits default values (context_window=131072, max_tokens=8192, reasoning=False)

## Testing

- `tests/` exists but has no tests yet
- `pytest` available as dev dependency: `pip install -e ".[dev]"`
- No CI/CD configured

## Quirks

- `config.yaml` with relative `registry:` path is resolved relative to config's parent directory, not CWD
- `serve.py` writes Pi models to `~/.pi/agent/models.json` on startup and after each refresh — only writes if the `.pi/agent/` directory exists