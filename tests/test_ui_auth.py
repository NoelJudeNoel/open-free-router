"""Tests for auth.py and the UI's enforcement of it on write endpoints."""
import http.client
import http.server
import json
import stat
import threading
import time
from unittest.mock import patch

import pytest

from open_free_router import ui
from open_free_router.auth import check_auth, get_or_create_token
from open_free_router.config import Config
from open_free_router.registry import Registry
import open_free_router.sync as _sync


@pytest.fixture(autouse=True)
def _isolate_sync_paths(tmp_path):
    """Redirect every agent-config path the UI's POST handlers reach (via
    write_pi_models / sync_all) to tmp_path, so a test that POSTs to
    /api/providers or /api/refresh can't overwrite the real ~/.pi,
    ~/.omp, ~/.config/opencode, ~/.hermes, or the real backup dir.
    """
    paths = {
        "PI_MODELS_PATH": tmp_path / "pi.json",
        "OMP_MODELS": tmp_path / "omp.yml",
        "OMP_CONFIG": tmp_path / "omp_cfg.yml",
        "OPENCODE_CONFIG": tmp_path / "opencode.json",
        "HERMES_CONFIG": tmp_path / "hermes.yaml",
        "BACKUP_DIR": tmp_path / "backups",
    }
    with patch.multiple(_sync, **paths):
        yield


class _FakeHeaders(dict):
    def get(self, key, default=""):
        return super().get(key, default)


def test_get_or_create_token_creates_file_once(tmp_path):
    tok1 = get_or_create_token(tmp_path)
    assert tok1
    path = tmp_path / "ui.token"
    assert path.exists()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600

    # Second call must return the same token, not regenerate it.
    tok2 = get_or_create_token(tmp_path)
    assert tok1 == tok2


def test_check_auth_accepts_valid_bearer_token():
    headers = _FakeHeaders({"Authorization": "Bearer secret123"})
    assert check_auth(headers, "secret123") is True


def test_check_auth_rejects_missing_or_wrong_token():
    assert check_auth(_FakeHeaders({}), "secret123") is False
    assert check_auth(_FakeHeaders({"Authorization": "Bearer wrong"}), "secret123") is False
    assert check_auth(_FakeHeaders({"Authorization": "secret123"}), "secret123") is False  # no "Bearer "


def test_check_auth_false_when_server_has_no_token():
    headers = _FakeHeaders({"Authorization": "Bearer whatever"})
    assert check_auth(headers, "") is False


# ---------------------------------------------------------------------------
# End-to-end: real HTTP server, real requests, exercising do_POST's auth gate.
# ---------------------------------------------------------------------------

def _start_ui_server(tmp_path, token="test-token"):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("proxy:\n  host: 127.0.0.1\n  port: 8337\nui:\n  host: 127.0.0.1\n  port: 0\n")
    cfg = Config(config_path=config_path)
    reg = Registry({})

    ui._UIHandler.cfg = cfg
    ui._UIHandler.reg = reg
    ui._UIHandler.config_path = cfg.path
    ui._UIHandler.token = token

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ui._UIHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    return srv, srv.server_address[1]


def test_post_without_token_is_rejected(tmp_path):
    srv, port = _start_ui_server(tmp_path)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/api/refresh", body="{}",
                      headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 401
        body = json.loads(resp.read())
        assert body["error"] == "unauthorized"
    finally:
        srv.shutdown()


def test_post_with_wrong_token_is_rejected(tmp_path):
    srv, port = _start_ui_server(tmp_path)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/api/refresh", body="{}",
                      headers={"Content-Type": "application/json",
                                "Authorization": "Bearer nope"})
        resp = conn.getresponse()
        assert resp.status == 401
        resp.read()
    finally:
        srv.shutdown()


def test_post_with_correct_token_is_accepted(tmp_path):
    srv, port = _start_ui_server(tmp_path, token="right-token")
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/api/providers", body=json.dumps({
            "name": "test-provider",
            "base_url": "http://127.0.0.1:8337/v1",
            "upstream_url": "https://example.com/v1",
            "api_key": "sk-test",
            "models": ["m1"],
        }), headers={"Content-Type": "application/json",
                     "Authorization": "Bearer right-token"})
        resp = conn.getresponse()
        body = json.loads(resp.read())
        assert resp.status == 200
        assert body["ok"] is True
    finally:
        srv.shutdown()


def test_get_endpoints_do_not_require_auth(tmp_path):
    """Read-only endpoints stay open — only POST is gated."""
    srv, port = _start_ui_server(tmp_path)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/status")
        resp = conn.getresponse()
        assert resp.status == 200
        resp.read()
    finally:
        srv.shutdown()


def test_api_health_returns_ok(tmp_path):
    """GET /api/health is an unauthenticated readiness probe."""
    srv, port = _start_ui_with_registry(tmp_path, _populated_registry(), token="tok")
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/health")
        resp = conn.getresponse()
        data = json.loads(resp.read())
        assert resp.status == 200
        assert data["ok"] is True
        assert data["service"] == "open-free-router-ui"
        assert "version" in data
        assert data["providers"] == 2
    finally:
        srv.shutdown()


def test_api_health_503_when_scheduler_errored(tmp_path):
    """A failed scheduler cycle flips /api/health to 503 so monitoring
    can page without the dashboard token."""
    srv, port = _start_ui_with_registry(tmp_path, _populated_registry(), token="tok")
    try:
        ui._UIHandler.scheduler_status = {"last_ok": None, "last_error": "boom"}
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/health")
        resp = conn.getresponse()
        data = json.loads(resp.read())
        assert resp.status == 503
        assert data["ok"] is False
        assert data["scheduler_last_error"] == "boom"
    finally:
        srv.shutdown()
# ── UI business logic tests (GET endpoints, POST with valid token) ──

def _populated_registry():
    return Registry({
        "openrouter": {
            "upstream_url": "https://openrouter.ai/api/v1",
            "api_key": "sk-or-1234567890abcdef",
            "prefix": "or",
            "models": [{"id": "m1:free", "context_window": 32000, "max_tokens": 4096}],
        },
        "nvidia-nim": {
            "upstream_url": "https://api.nv.nim/v1",
            "api_key": "sk-nv-secret",
            "prefix": "nv",
            "models": [
                {"id": "glm-5.2", "context_window": 131072, "max_tokens": 4096, "reasoning": True},
            ],
        },
    })


def _start_ui_with_registry(tmp_path, reg, token="test-token"):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("proxy:\n  host: 127.0.0.1\n  port: 8337\nui:\n  host: 127.0.0.1\n  port: 0\n")
    cfg = Config(config_path=config_path)

    ui._UIHandler.cfg = cfg
    ui._UIHandler.reg = reg
    ui._UIHandler.config_path = cfg.path
    ui._UIHandler.token = token

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ui._UIHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    return srv, srv.server_address[1]


def test_api_status_returns_providers_and_tiers(tmp_path):
    """GET /api/status returns provider list and tier routing info."""
    reg = _populated_registry()
    srv, port = _start_ui_with_registry(tmp_path, reg)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/status")
        resp = conn.getresponse()
        data = json.loads(resp.read())
        assert resp.status == 200
        assert len(data["providers"]) == 2
        names = [p["name"] for p in data["providers"]]
        assert "openrouter" in names
        assert "nvidia-nim" in names
        # Tier routing info is present (even if pools are empty)
        assert "tiers" in data
        assert "high" in data["tiers"]
        assert "mid" in data["tiers"]
        assert "low" in data["tiers"]
        assert "scheduler" in data
    finally:
        srv.shutdown()


def test_api_models_returns_grouped_models(tmp_path):
    """GET /api/models returns models grouped by provider."""
    reg = _populated_registry()
    srv, port = _start_ui_with_registry(tmp_path, reg)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/models")
        resp = conn.getresponse()
        data = json.loads(resp.read())
        assert resp.status == 200
        assert "openrouter" in data
        assert "nvidia-nim" in data
        assert len(data["openrouter"]) == 1
        assert data["openrouter"][0]["id"] == "m1:free"
        assert data["nvidia-nim"][0]["id"] == "glm-5.2"
        assert data["nvidia-nim"][0]["reasoning"] is True
    finally:
        srv.shutdown()


def test_api_providers_get_returns_masked_keys(tmp_path):
    """GET /api/providers returns masked API keys, not full secrets."""
    reg = _populated_registry()
    srv, port = _start_ui_with_registry(tmp_path, reg)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/providers")
        resp = conn.getresponse()
        data = json.loads(resp.read())
        assert resp.status == 200
        providers = {p["name"]: p for p in data["providers"]}
        assert "openrouter" in providers
        # Key must be masked (shows only first 8 + last 4 chars)
        masked = providers["openrouter"]["api_key"]
        assert "..." in masked
        assert masked.startswith("sk-or-")
        assert masked.endswith("cdef")
        assert "1234567890" not in masked  # middle part must be hidden
    finally:
        srv.shutdown()


def test_api_config_get_returns_current_config(tmp_path):
    """GET /api/config returns the YAML content of the config file."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("proxy:\n  host: 127.0.0.1\n  port: 9999\n")
    cfg = Config(config_path=config_path)
    reg = _populated_registry()

    ui._UIHandler.cfg = cfg
    ui._UIHandler.reg = reg
    ui._UIHandler.config_path = cfg.path
    ui._UIHandler.token = "tok"

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ui._UIHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        conn.request("GET", "/api/config")
        resp = conn.getresponse()
        data = json.loads(resp.read())
        assert resp.status == 200
        assert "proxy:" in data["yaml"]
        assert "port: 9999" in data["yaml"]
        # effective config includes defaults not present in the raw file
        assert "effective" in data
        assert data["effective"]["refresh_interval_hours"] == 12
        assert data["effective"]["upstream_timeout"] == 120
        assert data["effective"]["proxy_port"] == 9999
    finally:
        srv.shutdown()


def test_api_config_post_saves_config(tmp_path):
    """POST /api/config with valid YAML saves the file."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("proxy:\n  host: 127.0.0.1\n  port: 8337\n")
    cfg = Config(config_path=config_path)
    reg = Registry({})

    ui._UIHandler.cfg = cfg
    ui._UIHandler.reg = reg
    ui._UIHandler.config_path = cfg.path
    ui._UIHandler.token = "tok"

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ui._UIHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    try:
        new_yaml = "proxy:\n  host: 0.0.0.0\n  port: 8888\nrefresh_interval_hours: 6\n"
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        conn.request("POST", "/api/config",
                     body=json.dumps({"yaml": new_yaml}),
                     headers={"Content-Type": "application/json",
                              "Authorization": "Bearer tok"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        assert resp.status == 200
        assert data["ok"] is True
        # Verify the file was actually written
        saved = config_path.read_text()
        assert "port: 8888" in saved
        assert "refresh_interval_hours: 6" in saved
    finally:
        srv.shutdown()


def test_api_config_post_rejects_invalid_yaml(tmp_path):
    """POST /api/config with invalid YAML must be rejected with 400."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("")
    cfg = Config(config_path=config_path)
    reg = Registry({})

    ui._UIHandler.cfg = cfg
    ui._UIHandler.reg = reg
    ui._UIHandler.config_path = cfg.path
    ui._UIHandler.token = "tok"

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ui._UIHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        conn.request("POST", "/api/config",
                     body=json.dumps({"yaml": "[[invalid: yaml: [}}"}),
                     headers={"Content-Type": "application/json",
                              "Authorization": "Bearer tok"})
        resp = conn.getresponse()
        assert resp.status == 400
        resp.read()
    finally:
        srv.shutdown()


def test_api_providers_post_adds_new_provider(tmp_path):
    """POST /api/providers with valid data adds a new provider."""
    reg = _populated_registry()
    srv, port = _start_ui_with_registry(tmp_path, reg, token="tok")
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/api/providers",
                     body=json.dumps({
                         "name": "custom-provider",
                         "base_url": "http://127.0.0.1:8337/v1",
                         "api_key": "sk-custom",
                         "models": ["custom-model"],
                     }),
                     headers={"Content-Type": "application/json",
                              "Authorization": "Bearer tok"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        assert resp.status == 200
        assert data["ok"] is True
        assert data["provider"] == "custom-provider"
        # Verify it was actually added
        assert "custom-provider" in reg.providers
        assert reg.providers["custom-provider"].effective_key == "sk-custom"
    finally:
        srv.shutdown()


def test_api_providers_post_rejects_missing_name(tmp_path):
    """POST /api/providers without a name must be rejected."""
    reg = _populated_registry()
    srv, port = _start_ui_with_registry(tmp_path, reg, token="tok")
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/api/providers",
                     body=json.dumps({"base_url": "http://example.com"}),
                     headers={"Content-Type": "application/json",
                              "Authorization": "Bearer tok"})
        resp = conn.getresponse()
        assert resp.status == 400
        resp.read()
    finally:
        srv.shutdown()


def test_api_refresh_works_with_source(tmp_path):
    """POST /api/refresh with a source triggers a refresh call."""
    reg = _populated_registry()
    srv, port = _start_ui_with_registry(tmp_path, reg, token="tok")
    try:
        with patch("open_free_router.refresh.refresh") as mock_refresh:
            mock_refresh.return_value = {"openrouter": False}
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/api/refresh",
                         body=json.dumps({"provider": "openrouter"}),
                         headers={"Content-Type": "application/json",
                                  "Authorization": "Bearer tok"})
            resp = conn.getresponse()
            data = json.loads(resp.read())
            assert resp.status == 200
            assert data["ok"] is True
            # refresh was called with the correct provider name
            mock_refresh.assert_called_once()
            assert mock_refresh.call_args[1]["provider_name"] == "openrouter"
    finally:
        srv.shutdown()


def test_api_status_returns_scheduler_status(tmp_path):
    """GET /api/status reports scheduler status from the shared dict."""
    reg = _populated_registry()
    config_path = tmp_path / "config.yaml"
    config_path.write_text("proxy:\n  host: 127.0.0.1\n  port: 8337\nui:\n  host: 127.0.0.1\n  port: 0\n")
    cfg = Config(config_path=config_path)

    ui._UIHandler.cfg = cfg
    ui._UIHandler.reg = reg
    ui._UIHandler.config_path = cfg.path
    ui._UIHandler.token = "tok"
    ui._UIHandler.scheduler_status = {"last_ok": "2025-01-01T00:00:00", "last_error": None}

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ui._UIHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        conn.request("GET", "/api/status")
        resp = conn.getresponse()
        data = json.loads(resp.read())
        assert data["scheduler"]["last_ok"] == "2025-01-01T00:00:00"
        assert data["scheduler"]["last_error"] is None
    finally:
        srv.shutdown()

