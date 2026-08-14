# Changelog

All notable changes to open-free-router are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/).

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
