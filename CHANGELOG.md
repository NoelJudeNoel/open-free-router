# Changelog

All notable changes to open-free-router are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/).

## [0.3.0] - 2026-09-02

Probe-then-trust release: refresh no longer trusts /v1/models listings
blindly. Born from the 2026-09-02 audit that found NVIDIA NIM listing 54
chat-capable models of which only 9 answered a real request -- including
production entry `moonshotai/kimi-k2.6`, which 404'd ("Function not
found") while sitting in the registry as healthy.

### Added
- **Live model verification (`probe.py`)** — after each source's
  structural free-detection, every candidate model now receives one
  minimal real chat completion (`"Say pong"`, max_tokens=16) before it
  may enter the registry. Verdicts: 200 → verified; 429/5xx → alive
  (kept — tier failover absorbs saturation); 400/404 → dead (400 gets
  one fuller-payload retry first, strict upstreams reject tiny
  max_tokens); 401/403 → auth; network errors → inconclusive (kept,
  conservatively). Batch guards: all-auth/all-inconclusive keeps the old
  list (a rotated key must never wipe the registry), and a run of 5
  consecutive timeouts trips a circuit breaker so a hung upstream can't
  stall a whole refresh cycle.
- **`verify_models` config knob** (default on) and
  `open-free-router refresh --skip-verify` to opt out per invocation.
- **Kimi K3 in tier/high** — all collected K3 instances pool into the
  high tier with priority order sensenova → nvidia-nim → leuai.
- **NIM funnel redesign** — the 7-entry KNOWN_FREE allowlist is retired
  (it was stale in both directions: listed 3 models absent from the live
  catalog, blocked newer live ones). Detection is now "live catalog
  minus non-chat endpoints" (embed/rerank/guard/vision/riva/reward/
  video/parse/cosmos blocklist); usability is probe's job.

### Changed
- **registry.default.yaml** refreshed to the 2026-09-02 verified NIM
  catalog snapshot (deepseek-v4-flash/-pro dated builds, kimi-k3,
  nemotron-3.5-lightning/super/nano-omni, gpt-oss-120b/20b, gemma-4-31b,
  muse-glimmer-30b, diffusiongemma-26b-a4b-it, laguna-xs-2.1; removed
  kimi-k2.6 (404-dead), step-3.7-flash / glm-5.2 / mistral-medium-3.5
  (no longer listed)); Zen bootstrap list synced to the live free
  catalog; sensenova template gains deepseek-v4-pro + kimi-k3.
- **TIERS["mid"] drops `step-3.7-flash`** — its last free sources are
  gone (NIM no longer lists it; Nous' copy 400s "missing tags" on every
  chat payload shape).

- **Ant Group Ling refresh source (`ant-ling`)** — pluggable fetch() for
  Ant's Ling API (Ling-3.0-flash), completing the SOURCE_MAP entry that
  registry configs already referenced.

### Fixed
- **google-ai-studio fetch never worked (401)** — native v1beta REST
  rejects Bearer auth; switched to `x-goog-api-key`. Masked until now by
  `auto_refresh: false` in every shipped config. KNOWN_FREE also gains
  gemini-3.7-flash so a successful refresh no longer washes it out.
- **proxy: clamp `max_tokens` to the concrete model's registered cap** —
  agents (Hermes/OMP/OpenCode) can send max_tokens far above the real
  upstream limit on length-continuation retries (e.g. 32768 to Groq),
  which 400s deterministically. The tier path already clamped via
  `_patch_model()`; the direct concrete-model path now does too.
- **teamorouter / bearlab removed from upstream config** (2026-09-02):
  teamo wallet empty (all models 400), bearlab token invalid (401);
  template no longer bootstraps teamorouter.

## [0.3.1] - 2026-09-02

OpenCode Zen session-header release. Zen's relay has hardened in three
stages (2026-09-05 session header, 2026-09-22 per-model endpoint split,
2026-09-23 FreeTierError request-shape gate); this release clears the
first of them on our side and documents the rest honestly.

### Fixed
- **probe & proxy: inject `x-opencode-session` for OpenCode Zen** —
  `probe.py` now sends a stable session header on every Zen candidate
  (clearing the 2026-09-05 `400 MissingSessionID` gate); `upstream.py`
  synthesizes a per-conversation id for real traffic (prefers an incoming
  `x-opencode-session` from DSH's dsh-opencode-session plugin, otherwise
  derives from the first user message — stable across a conversation's
  turns, so the relay's prompt-cache affinity stays warm). **This does
  not defeat the 2026-09-23 `FreeTierError` gate.** We reproduced the
  full identity stack the gateway asks for — anonymous `Bearer public`,
  OpenCode User-Agent, `x-opencode-client`, `project`, canonical
  `x-opencode-session`, per-request `x-opencode-request`, a `tools` array
  containing `bash`/`read`, `stream: true`, and the correct per-model
  endpoint (`muse-spark-*` → `/v1/responses`, the rest →
  `/v1/chat/completions`) — and every combination still returns 403
  `FreeTierError`. Third-party state of the art (pi-opencode-zen, now
  DEPRECATED/UNUSABLE) agrees this is not bypassable by headers or client
  emulation, pointing at egress-IP reputation plus a shared per-IP
  anonymous quota. See AGENTS.md "Supported providers" for the full
  timeline.
- **AGENTS.md updated** with the three-stage Zen gate timeline and the
  reproduction evidence (including the control run showing we do reach
  the gateway and are refused past it).
- Also carried in this release: the daemon fix below — a stale orphan
  process had been holding ports 8337/9057 since mid-September, so
  systemd's `MainPID=0` left it unkillable and restarts silently failed;
  every earlier "restart" was served by that orphan running old code
  while hot-reloading the new registry. Killed and cleanly restarted.

## [0.2.0] - 2026-08-16

Tier observability release: per-request failover trails surfaced across
four channels, plus Google AI Studio hardening (new gemini-3.7-flash,
field-sanitization for the OpenAI SDK).

### Added
- **Tier observability (T0-T3)** — every tier/* request now carries a
  full failover trail, surfaced 4 ways:
  - **T0 response headers** (always on, streaming-safe — sent before
    the first SSE byte): X-OFR-Trace, X-OFR-Tier, X-OFR-Served-By,
    X-OFR-Attempts, X-OFR-Filtered, X-OFR-Cascade, X-OFR-Cooldown-Set,
    X-OFR-Request-Context.
  - **T1 failure body**: 429 responses include error.x_ofr.attempts[]
    so apps can distinguish upstream quota exhaustion from total failure.
  - **T2 opt-in debug body**: sending X-OFR-Debug: true enriches
    non-streaming 2xx with a full x_ofr JSON (attempts with
    status/ms/retry_after, filtered_keys, cascade_path, served_by).
  - **T3 structured logs**: [tier:{trace_id}] lines correlate app-side
    headers to daemon logs.
  - TierTrace per-request container; tier_filtered_instances() makes
    context-window pre-filtering observable. Design principle:
    *no-feel is default, transparency is opt-in*.
- **Google AI Studio: gemini-3.7-flash** (1M context, 64K output,
  thinking) added to registry + TIERS["high"] ahead of 3.6; Pi/DSH
  configs synced with correct contextWindow/maxTokens/reasoning.
- **Context-window corrections**: google-ai-studio models now carry the
  real context_window: 1048576 (was the 131072 default, which would
  have pre-filtered gemini out of tier/high for requests >128K tokens).
- **Tests**: 238 across 20 files (82% coverage; tiers.py 100%,
  upstream.py 90%).

### Fixed
- **OpenAI-SDK field sanitization**: strip agent/SDK extension fields
  before forwarding to upstreams that reject them with HTTP 400 —
  include_reasoning, reasoning, extra_body, x_options, plus the SDK
  defaults frequency_penalty, logit_bias, seed (Google's OpenAI-
  compatible endpoint rejects these; openai SDK sends frequency_penalty
  on every request, so any gemini call via DSH/Pi used to 400).
- **Chunked request bodies decoded**: the openai SDK switches to Transfer-Encoding: chunked for large conversation histories; Python http.server used to reject those with a bare 400 (no body), which agents (DSH/Pi) misreport as CONTEXT_WINDOW_EXCEEDED. The proxy now decodes chunked bodies (RFC 7230).
- **Lenient JSON repair**: some clients send key:value JSON without quotes (e.g. {model:gai/...,messages:[...]}); the proxy repairs and forwards these instead of 400 invalid json.
- **OpenAI SDK 6.26 defaults stripped**: store, metadata, logprobs (plus frequency_penalty, logit_bias, seed, reasoning, include_reasoning, extra_body, x_options) are removed before forwarding - Google rejects them with 400.
- **prefix/upstream_id model format**: nv/z-ai/glm-5.2 and similar (short channel prefix + upstream_id) now match and forward correctly (previously left un-rewritten, causing upstream 404).
- Streaming trace: ms timing and status no longer raise
  UnboundLocalError on the success path.
- X-OFR-Debug opt-in body now uses safe header access (no
  AttributeError when no HTTP request context exists).

## [0.1.0] - 2026-08-14

First versioned release. Baseline: cross-tier cascade failover, tier
routing hardening, sync placeholder-key hygiene, registry hot-reload,
81% test coverage (226 tests).

### Added
- **Cross-tier cascade failover** (`tier_cascade: true`): when a
  requested tier (high/mid/low) is fully rate-limited or quota-exhausted,
  the same request spills into the next-lower tier and returns 200 — the
  app never sees a 429. Strictly downward (high→mid→low); the user's
  tier choice is a ceiling.
- **Tier routing hardening**: `_normalize()` strips `:free`/`-free`
  suffixes and trailing date suffixes (`-0731`, `-20250814`), so
  cross-provider free variants and dated builds match their tier logical
  IDs. Low tier no longer duplicates high/mid models.
- **Registry hot-reload**: the daemon reloads registry.yaml on `SIGUSR1`
  (sent by `open-free-router refresh`/`add` after saving) or via a
  10s mtime watchdog — no restart needed after CLI changes.
- **Sync placeholder-key hygiene**: all agent configs (Pi, OMP,
  OpenCode, Hermes) receive the placeholder key `open-free-router`
  instead of real upstream keys.
- **NVIDIA NIM dated-build support**: `deepseek-ai/deepseek-v4-flash-0731`
  is recognized (date suffix stripped before allowlist match) and served
  under the canonical id `deepseek-v4-flash` with the full dated
  upstream_id for correct forwarding.
- **`--version` flag** for the CLI.
- **Tests**: 226 across 20 files (81% line coverage); CI matrix now
  runs Python 3.11 / 3.12 / 3.13.

### Fixed
- `retry_after` HTTP-date parsing (was raising `ValueError` on
  Python 3.13).
- `refresh_interval_hours` clamped to a minimum of 1 (was allowing 0 →
  busy-loop scheduler).
- `rebuild_proxy_index()` now rebuilds the live proxy handler class
  (which carries the registry) instead of the base `_ProxyHandler`.
- Dead tier entry `ling-3.0-flash-free` removed (no provider ships it).
- Dead imports removed (proxy.py `Path`, serve.py `json/time/signal`).

### Changed
- `open-free-router refresh`/`add` now notify the running daemon to
  hot-reload after saving (previously required a manual restart).
- `main()` accepts an optional `args` parameter for testability.
- NVIDIA NIM refresh sends `User-Agent: open-free-router/0.1` (was
  missing; required on all upstream requests).
