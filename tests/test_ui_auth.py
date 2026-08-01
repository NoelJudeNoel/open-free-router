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
