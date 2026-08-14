# open-free-router — AGENTS.md

## Entrypoint

- CLI: `open-free-router` (defined in `pyproject.toml` → `open_free_router.cli:main`)
- Install: `pip install -e .` (venv required, Python 3.11+) or `scripts/install.sh`
- Dependencies: `pyyaml` + `requests` + `ruamel.yaml`; no web framework (stdlib `http.server`)
- Dev: `pip install -e ".[dev]"` adds `pytest`

## Commands

| Command | What it does |
|---|---|
| `open-free-router serve` | **★ One command:** proxy(8337) + UI(9057) + scheduler (configurable interval, default 12h). Single-instance guarded (pidfile+flock + port probe) |
| `open-free-router ui` | Web dashboard standalone |
| `open-free-router setup` | Interactive wizard: fill in API keys for all providers |
| `open-free-router refresh [--source NAME] [--dry-run]` | Poll provider APIs for free model changes; on a real (non-dry-run) change, saves registry.yaml and sends SIGUSR1 to the running daemon so it hot-reloads — no restart needed |
| `open-free-router add NAME --base-url URL [--upstream-url URL] [--model ID] [--auto-refresh]` | Add a provider to registry |
| `open-free-router sync [--agent pi,omp,opencode,hermes] [--diff]` | Sync registry to Pi/OMP/OpenCode/Hermes configs |

## Config

- `~/.config/open-free-router/config.yaml` (or `./config.yaml` in CWD)
- `registry.yaml` is the single source of truth for providers + models
- `registry.yaml` on first `serve` is auto-created from `registry.default.yaml`
- Both files get `.bak-YYYYMMDD-HHMMSS` backups on write (retention: 10, see `BACKUP_RETENTION` in registry.py)
- API keys live in `registry.yaml` — never commit
- `refresh_interval_hours` in config.yaml controls scheduler frequency (default 12)
- `upstream_timeout` in config.yaml controls upstream timeout (default 120s)
- `registry_git_history: false` by default — opt-in local git history of registry.yaml (never pushed; kept off because registry.yaml holds plaintext keys)
- UI auth token lives at `<config dir>/ui.token` (mode 0600), generated on first run; required as `Authorization: Bearer` for all dashboard POST endpoints

## Bootstrap

- `src/open_free_router/registry.default.yaml` — template with 9 upstream sources and model lists, no API keys
- `scripts/install.sh` — one-liner installer (git clone + venv + setup). `--with-systemd` for systemd auto-start
- `contrib/systemd/open-free-router.service` — systemd service unit (auto-restart on failure)
- First `serve` auto-bootstraps config + registry; `open-free-router setup` walks through key entry interactively
- On Linux with systemd: `bash install.sh --with-systemd` sets up auto-start via `open-free-router.service`

## Architecture

```
src/open_free_router/
├── __init__.py           # package marker
├── cli.py                # argparser → routes to serve/ui/refresh/add/sync/setup
├── config.py             # Config class: loads config.yaml, resolves paths
├── registry.py           # Registry CRUD: ProviderConfig + ModelInfo dataclasses, .bak pruning, optional git history
├── registry.default.yaml # Template with 9 upstream sources, no API keys
├── proxy.py              # Single-port proxy (8337), routes by model ID + tier IDs to upstream
├── tiers.py              # Tier routing: tier/high|mid|low → ordered pool of upstream instances, _INSTANCE_PRIORITY
├── upstream.py           # Tier forwarding driver: retry/cooldown/failover policy, TierExhaustedError, streaming
├── refresh.py            # Dispatches per-provider refresh; SOURCE_MAP + CANONICAL_UPSTREAM_URLS (for pinning)
├── refresh_sources/      # Pluggable fetch() per provider: openrouter, nvidia_nim, opencode_zen, sensenova, google_ai_studio, nous, poolside
├── serve.py              # Daemon: proxy + UI + scheduler + Pi models.json writer
├── sync.py               # Sync registry to Pi/OMP/OpenCode/Hermes configs (dedup-aware, ruamel for OMP)
├── ui.py                 # Web dashboard (9057): status, provider CRUD, refresh, config edit (token-gated POST)
├── auth.py               # Local UI auth token: get_or_create_token, constant-time Bearer check
├── _instance_guard.py    # Single-instance guard: port probe + pidfile flock (stops Errno-98 restart loops)
├── templates/            # UI templates (index.html)
└── web_static/           # UI static assets (CSS, JS)
```

## Key conventions

- **Single-port proxy (8337)** — all agents point to one base_url; routing by model ID via `_model_index`
- **Multi-threaded** — both proxy and UI use `ThreadingHTTPServer` (stdlib) to avoid head-of-line blocking
- **User-Agent** — upstream requests include `User-Agent: open-free-router/0.1` to avoid Cloudflare 1010 blocks (Python urllib default triggers bot detection)
- **Timeout** — upstream timeout is 120s (configurable via `upstream_timeout` in config.yaml)
- **Zero web framework** — uses stdlib `http.server`; no Flask/FastAPI/uvicorn
- **Refresh sources** are pluggable modules. Each must export `fetch(upstream_url, api_key) -> list[ModelInfo]`. `refresh()` returns *did the list actually change* — callers gate writes on `any(results.values())` so no-op cycles don't rewrite backups/sync files
- **upstream_url pinning** — for built-in providers (in `SOURCE_MAP`), `upstream_url` is pinned to the canonical value from `registry.default.yaml`; submitted overrides are ignored (response flags `upstream_url_pinned`). Custom providers keep full freedom
- **Pi models.json** written by `serve.py` on startup and after each refresh, and by `ui.py` on changes. Format: `{providers: {name: {baseUrl, models: [...]}}}`. All providers point to local proxy; routing is by model ID
- **Scheduler interval** configurable via `config.yaml: refresh_interval_hours` (default 12)
- **Registry hot-reload** — the daemon watches registry.yaml's mtime (every ~10s) AND handles SIGUSR1 (sent by `open-free-router refresh`/`add` after saving). Either triggers an in-process reload of the registry into the proxy/UI/scheduler without restarting serve. Agent configs (Pi/OMP/OpenCode/Hermes) are NOT rewritten by a reload — that stays the job of `open-free-router sync` / the scheduler cycle
- **Health endpoints** — proxy `GET /healthz` and UI `GET /api/health` (both unauthenticated readiness probes, no keys revealed; UI one includes scheduler last_ok/last_error). The shipped systemd unit uses `HealthCheckCommand=curl -fsS http://127.0.0.1:8337/healthz` (needs systemd >= 253) plus a `StartLimitBurst=5` crash-loop guard. Version: `open-free-router --version` / `__version__` (single source `src/open_free_router/__init__.py`, mirrored in pyproject.toml; bump in lockstep with CHANGELOG.md)
- **Single-instance guard** — `_instance_guard.py` probes ports + takes a pidfile flock before `serve` starts; refuses/exit otherwise (prevents systemd auto-restart loops)
- **UI auth** — all dashboard POST endpoints require `Authorization: Bearer <token>` (`ui.token`, constant-time compare via `hmac.compare_digest`); `token=""` disables (tests only)
- **ModelInfo** fields: `id` (short display name, e.g. `glm-5.2`), `upstream_id` (optional, e.g. `z-ai/glm-5.2`, falls back to `id`), `name`, `context_window`, `max_tokens`, `reasoning`
- **ProviderConfig** fields: `name`, `base_url`, `upstream_url`, `api_key`/`api_keys`, `models`, `auto_refresh`, `refresh_method`, `prefix` (short channel prefix, e.g. `nv`, `or`; falls back to provider name)
- **config.yaml `registry:` path** resolved relative to config's parent directory, not CWD
- **4 model ID formats** — proxy resolves all of them:
  1. bare id       `glm-5.2`
  2. prefix/id     `nv/glm-5.2`
  3. upstream_id   `z-ai/glm-5.2`
  4. provider/upstream_id `nvidia-nim/z-ai/glm-5.2` (OMP format)
- **Tier routing** — virtual IDs `tier/high|mid|low` expand to an ordered pool of upstream instances (by `_INSTANCE_PRIORITY` then context window), with per-instance retry (1), cooldown (60s, honoring Retry-After), and automatic failover; context-window pre-filter; exhaustion → 429 with last real status. Streaming switches only before first byte. `reset_tier_state()` is wired into `rebuild_proxy_index()` (called by every registry-change path). The tier lists and priority table in `tiers.py` are hand-maintained, not derived from refresh
- **Internal-key hygiene** — `proxy.py` stashes `_endpoint_path`/`_headers` on the request dict; `upstream._patch_model()` strips `_`-prefixed keys before serializing so routing internals never leak into upstream request bodies
- **Tier observability (per-request trail)** — `TierTrace` dataclass collects the full failover trail for each `tier/*` request: which instances were filtered by context-window, which failed (status/retry_after/ms), which were cooled down, and which ultimately served. The proxy surfaces this via 4 channels: **T0 response headers** (`X-OFR-Trace`/`X-OFR-Tier`/`X-OFR-Served-By`/`X-OFR-Attempts`/`X-OFR-Filtered`/`X-OFR-Cascade`/`X-OFR-Cooldown-Set`/`X-OFR-Request-Context`) — always on, zero body impact, works for streaming (sent before first SSE byte); **T1 failure body** — 429 `error.x_ofr.attempts[]` with full trail; **T2 opt-in debug body** — non-streaming 2xx gets `x_ofr` field when request sends `X-OFR-Debug: true`; **T3 structured log** — `[tier:{trace_id}]` lines correlate with headers. Design principle: "no-feel is default, transparency is opt-in" — automatic failover/cascade is invisible by design, but the black box is openable on demand
- **Sync** — `open-free-router sync` writes Pi models.json, OMP models.yml (ruamel.yaml round-trip, preserves hand edits), OpenCode opencode.json, and ensures Hermes custom_providers entry from registry; backs up existing configs to `~/.openclaw/agent-backup/`
- **Sync dedup** — before writing, removes all providers pointing to local proxy (baseURL contains 127.0.0.1/localhost) to prevent duplicate accumulation; Pi always overwrites entire file

## Scripts

- `scripts/install.sh` — one-liner: clone → venv → pip install → optional systemd
- `contrib/systemd/open-free-router.service` — systemd unit file for Linux auto-start + auto-restart

## Testing

- 238 tests across 20 files (run: `pip install -e ".[dev]" && python3 -m pytest tests/ -v`):
  - `test_tier_routing.py` (77) — tier pool expansion, priority ordering, context pre-filter, failover/cooldown, upstream path prefix, _normalize suffix stripping, regression tests for all tier-hardening fixes, TierTrace per-request trail (filtered/error/ok attempts, cascade path, cooldowns set, served_by)
  - `test_registry.py` (24) — ModelInfo, ProviderConfig, Registry CRUD, proxy index
  - `test_ui_auth.py` (20) — token gating of POST endpoints + business logic (status, config, models, providers, refresh, /api/health)
  - `test_sync.py` (12) — Pi/OpenCode/Hermes sync, placeholder-key enforcement, stale removal, dispatch
  - `test_refresh_sources_new.py` (11) + `test_refresh_sources_second_audit.py` (7) — refresh-source allowlist/parse behavior
  - `test_proxy_hardening.py` (13) — body limits, content-length handling, error paths, /healthz, tier observability (X-OFR headers, opt-in x_ofr body)
  - `test_hot_reload.py` (9) — daemon registry hot-reload (watchdog + SIGUSR1), CLI daemon notify
  - `test_instance_guard.py` (8), `test_refresh_source_nvidia_nim.py` (10), `test_registry_git_history.py` (7), `test_refresh_sources_second_audit.py` (7), `test_config.py` (6), `test_cli.py` (6), `test_scheduler.py` (6), `test_sync_omp.py` (5), `test_upstream_url_anchoring.py` (5), `test_refresh.py` (4), `test_production_incident_nous_sensenova.py` (4), `test_streaming.py` (2), `test_serve.py` (2)
- `tests/e2e_test.py` is a manual live smoke-test script (real API calls + real agent configs) — excluded from `pytest` via `addopts = --ignore=...`; run it directly: `python tests/e2e_test.py`
- CI: `.github/workflows/tests.yml` runs the suite on Python 3.11 + 3.12 + 3.13
- Some refresh sources hit live provider APIs and are not covered by CI (network-dependent); verify with `open-free-router refresh --source NAME` when changing them

## Supported providers (7)

openrouter, nvidia-nim, opencode-zen-free, sensenova, google-ai-studio, nous, poolside

- DeepSeek was removed as a direct provider (2026-07): its models are reached via NVIDIA NIM / SenseNova instead
- Groq and StepFun were removed as direct providers (2026-08): StepFun's model is still reachable via NVIDIA NIM's own separately-hosted copy (`stepfun-ai/step-3.7-flash`)
- Free-model detection: OpenRouter/Nous/SenseNova read `pricing` fields (structural); OpenCode Zen uses `-free` suffix + hardcoded exceptions; NVIDIA NIM/Google AI Studio/Poolside use hand-maintained allowlists (`KNOWN_FREE`) that only get verified when a real key runs refresh

## Related

- `registry.default.yaml` defines the canonical model list for each provider (also the source of pinned upstream URLs)
- `refresh_sources/*.py` implement `fetch()` for auto-refresh capable providers
- `sync.py`'s `write_pi_models()` is called by both `serve.py` (on startup + refresh) and `ui.py`
- Sync removes stale local-proxy entries before re-adding from registry (prevents duplicate accumulation)
- `docs/QUICKSTART.md` / `docs/QUICKSTART.en.md` — beginner step-by-step walkthroughs
