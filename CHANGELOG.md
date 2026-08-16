# Changelog

All notable changes to open-free-router are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/).

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
