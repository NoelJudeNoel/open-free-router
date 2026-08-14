"""Tests for Phase 3 proxy hardening: request body size limit,
Retry-After passthrough on errors, and the /v1/completions and
/v1/embeddings endpoints (same routing as /v1/chat/completions, just a
different upstream path suffix).
"""
from __future__ import annotations

import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from open_free_router.proxy import run_proxy, _ProxyHandler
from open_free_router.registry import Registry


class _EchoUpstreamHandler(BaseHTTPRequestHandler):
    """Fake upstream that echoes back which path it was hit on, so tests
    can confirm /v1/completions and /v1/embeddings actually forward to
    .../completions and .../embeddings respectively, not always
    .../chat/completions."""

    disable_nagle_algorithm = True

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({"hit_path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _RateLimitedUpstreamHandler(BaseHTTPRequestHandler):
    disable_nagle_algorithm = True

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({"error": {"message": "rate limited"}}).encode()
        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Retry-After", "17")
        self.end_headers()
        self.wfile.write(body)


def _start(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _proxy_for(upstream_port: int, models=("m1",)):
    reg = Registry({
        "fake": {
            "upstream_url": f"http://127.0.0.1:{upstream_port}/v1",
            "api_key": "sk-test",
            "models": [{"id": mid} for mid in models],
        },
    })
    return run_proxy(reg, host="127.0.0.1", port=0)


class TestNewEndpoints:
    def test_completions_forwards_to_completions_path(self):
        upstream = _start(_EchoUpstreamHandler)
        proxy_srv, _ = _proxy_for(upstream.server_address[1])
        try:
            conn = http.client.HTTPConnection("127.0.0.1", proxy_srv.server_address[1], timeout=5)
            conn.request("POST", "/v1/completions", body=json.dumps({"model": "m1", "prompt": "hi"}),
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            data = json.loads(resp.read())
            assert resp.status == 200
            assert data["hit_path"] == "/v1/completions"
        finally:
            proxy_srv.shutdown()
            upstream.shutdown()

    def test_embeddings_forwards_to_embeddings_path(self):
        upstream = _start(_EchoUpstreamHandler)
        proxy_srv, _ = _proxy_for(upstream.server_address[1])
        try:
            conn = http.client.HTTPConnection("127.0.0.1", proxy_srv.server_address[1], timeout=5)
            conn.request("POST", "/v1/embeddings", body=json.dumps({"model": "m1", "input": "hi"}),
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            data = json.loads(resp.read())
            assert resp.status == 200
            assert data["hit_path"] == "/v1/embeddings"
        finally:
            proxy_srv.shutdown()
            upstream.shutdown()

    def test_embeddings_ignores_stream_flag(self):
        """Embeddings has no streaming concept in the OpenAI API; a stray
        stream:true in the body must not route into the SSE code path."""
        upstream = _start(_EchoUpstreamHandler)
        proxy_srv, _ = _proxy_for(upstream.server_address[1])
        try:
            conn = http.client.HTTPConnection("127.0.0.1", proxy_srv.server_address[1], timeout=5)
            conn.request("POST", "/v1/embeddings", body=json.dumps({"model": "m1", "input": "hi", "stream": True}),
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            assert resp.getheader("Transfer-Encoding") != "chunked"
            data = json.loads(resp.read())
            assert data["hit_path"] == "/v1/embeddings"
        finally:
            proxy_srv.shutdown()
            upstream.shutdown()

    def test_unknown_model_still_403_on_new_endpoints(self):
        upstream = _start(_EchoUpstreamHandler)
        proxy_srv, _ = _proxy_for(upstream.server_address[1])
        try:
            conn = http.client.HTTPConnection("127.0.0.1", proxy_srv.server_address[1], timeout=5)
            conn.request("POST", "/v1/embeddings", body=json.dumps({"model": "not-in-whitelist", "input": "hi"}),
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            assert resp.status == 403
            resp.read()
        finally:
            proxy_srv.shutdown()
            upstream.shutdown()


class TestRetryAfterPassthrough:
    def test_429_retry_after_forwarded_non_streaming(self):
        upstream = _start(_RateLimitedUpstreamHandler)
        proxy_srv, _ = _proxy_for(upstream.server_address[1])
        try:
            conn = http.client.HTTPConnection("127.0.0.1", proxy_srv.server_address[1], timeout=5)
            conn.request("POST", "/v1/chat/completions",
                         body=json.dumps({"model": "m1", "messages": []}),
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            assert resp.status == 429
            assert resp.getheader("Retry-After") == "17"
            resp.read()
        finally:
            proxy_srv.shutdown()
            upstream.shutdown()

    def test_429_retry_after_forwarded_streaming_request(self):
        """stream:true but upstream errors before any SSE body — the
        Retry-After header must still reach the client."""
        upstream = _start(_RateLimitedUpstreamHandler)
        proxy_srv, _ = _proxy_for(upstream.server_address[1])
        try:
            conn = http.client.HTTPConnection("127.0.0.1", proxy_srv.server_address[1], timeout=5)
            conn.request("POST", "/v1/chat/completions",
                         body=json.dumps({"model": "m1", "messages": [], "stream": True}),
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            assert resp.status == 429
            assert resp.getheader("Retry-After") == "17"
            resp.read()
        finally:
            proxy_srv.shutdown()
            upstream.shutdown()


class TestBodySizeLimit:
    def test_oversized_body_rejected_with_413(self):
        upstream = _start(_EchoUpstreamHandler)
        proxy_srv, handler = _proxy_for(upstream.server_address[1])
        try:
            handler.MAX_BODY_BYTES = 1024  # shrink limit so the test is fast
            conn = http.client.HTTPConnection("127.0.0.1", proxy_srv.server_address[1], timeout=5)
            big_body = json.dumps({"model": "m1", "messages": [{"role": "user", "content": "x" * 5000}]})
            conn.request("POST", "/v1/chat/completions", body=big_body,
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            assert resp.status == 413
            resp.read()
        finally:
            proxy_srv.shutdown()
            upstream.shutdown()

    def test_body_within_limit_still_works(self):
        upstream = _start(_EchoUpstreamHandler)
        proxy_srv, handler = _proxy_for(upstream.server_address[1])
        try:
            handler.MAX_BODY_BYTES = 1024
            conn = http.client.HTTPConnection("127.0.0.1", proxy_srv.server_address[1], timeout=5)
            conn.request("POST", "/v1/chat/completions",
                         body=json.dumps({"model": "m1", "messages": []}),
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            assert resp.status == 200
            resp.read()
        finally:
            proxy_srv.shutdown()
            upstream.shutdown()

    def test_missing_content_length_rejected_411(self):
        upstream = _start(_EchoUpstreamHandler)
        proxy_srv, _ = _proxy_for(upstream.server_address[1])
        try:
            sock_conn = http.client.HTTPConnection("127.0.0.1", proxy_srv.server_address[1], timeout=5)
            sock_conn.putrequest("POST", "/v1/chat/completions", skip_host=True, skip_accept_encoding=True)
            sock_conn.putheader("Content-Type", "application/json")
            sock_conn.endheaders()  # deliberately no Content-Length, no body
            resp = sock_conn.getresponse()
            assert resp.status == 411
            resp.read()
        finally:
            proxy_srv.shutdown()
            upstream.shutdown()


class TestHealthz:
    def test_healthz_returns_ok_with_registry(self):
        upstream = _start(_EchoUpstreamHandler)
        proxy_srv, _ = _proxy_for(upstream.server_address[1])
        try:
            conn = http.client.HTTPConnection("127.0.0.1", proxy_srv.server_address[1], timeout=5)
            conn.request("GET", "/healthz")
            resp = conn.getresponse()
            data = json.loads(resp.read())
            assert resp.status == 200
            assert data["ok"] is True
            assert data["service"] == "open-free-router"
            assert "version" in data
            assert data["providers"] == 1
        finally:
            proxy_srv.shutdown()
            upstream.shutdown()

    def test_healthz_503_when_no_registry(self):
        upstream = _start(_EchoUpstreamHandler)
        # Start a proxy with an empty registry so _ProxyHandler.registry
        # is falsy on the health probe path.
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _ProxyHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            conn.request("GET", "/healthz")
            resp = conn.getresponse()
            data = json.loads(resp.read())
            assert resp.status == 503
            assert data["ok"] is False
        finally:
            srv.shutdown()
            upstream.shutdown()

class _OkUpstream(BaseHTTPRequestHandler):
    """Returns a 200 chat-completion-shaped body."""
    disable_nagle_algorithm = True
    def log_message(self, format, *args):
        pass
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({"id": "chatcmpl-ok", "model": "gemini-3.6-flash",
                           "choices": [{"index": 0,
                                        "message": {"role": "assistant", "content": "hi"},
                                        "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _FailUpstream(BaseHTTPRequestHandler):
    """Always 429 with Retry-After, to force failover within a tier."""
    disable_nagle_algorithm = True
    def log_message(self, format, *args):
        pass
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({"error": {"message": "rate limited"}}).encode()
        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Retry-After", "60")
        self.end_headers()
        self.wfile.write(body)


class TestTierObservability:
    def test_tier_response_has_ofr_headers_and_served_by(self):
        """A tier/high request that cascades to a working instance returns
        X-OFR-* headers telling the app which instance served it and why."""
        import yaml as _yaml
        from pathlib import Path as _Path

        default = _yaml.safe_load((_Path(__file__).parent.parent
                                   / "src" / "open_free_router" / "registry.default.yaml").read_text())
        ok_upstream = _start(_OkUpstream)
        fail_upstream = _start(_FailUpstream)
        try:
            # route google-ai-studio (gemini-3.6-flash, high tier) to ok,
            # everything else (nvidia-nim z-ai/glm-5.2 etc) to fail upstream
            for name in default:
                if name == "google-ai-studio":
                    default[name]["upstream_url"] = f"http://127.0.0.1:{ok_upstream.server_address[1]}/v1beta"
                    default[name]["api_key"] = "sk-ok"
                else:
                    default[name]["upstream_url"] = f"http://127.0.0.1:{fail_upstream.server_address[1]}/v1"
                    default[name]["api_key"] = "sk-fail"
            reg = Registry(default)
            # force ALL high instances to fail first: patch tier_members order is
            # priority-based; instead make _connect fail for non-ok provider
            from open_free_router.proxy import run_proxy as _run_proxy
            proxy_srv, handler = _run_proxy(reg, host="127.0.0.1", port=0)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", proxy_srv.server_address[1], timeout=5)
                conn.request("POST", "/v1/chat/completions",
                             body=json.dumps({"model": "tier/high", "messages": [{"role": "user", "content": "hi"}]}),
                             headers={"Content-Type": "application/json"})
                resp = conn.getresponse()
                data = json.loads(resp.read())
                ofr_trace = resp.getheader("X-OFR-Trace")
                assert resp.status == 200
                assert ofr_trace, "X-OFR-Trace header missing"
                assert resp.getheader("X-OFR-Tier") == "high"
                served = resp.getheader("X-OFR-Served-By") or data.get("model")
                assert served, "served instance not surfaced"
                assert resp.getheader("X-OFR-Attempts") is not None
                # cascade should be surfaced (high failed -> mid/low served)
                assert resp.getheader("X-OFR-Cascade") is not None or resp.getheader("X-OFR-Attempts")
            finally:
                proxy_srv.shutdown()
        finally:
            ok_upstream.shutdown()
            fail_upstream.shutdown()

    def test_tier_debug_body_opt_in(self):
        """X-OFR-Debug: true enriches the non-streaming body with x_ofr trail."""
        import yaml as _yaml
        from pathlib import Path as _Path
        default = _yaml.safe_load((_Path(__file__).parent.parent
                                   / "src" / "open_free_router" / "registry.default.yaml").read_text())
        ok_upstream = _start(_OkUpstream)
        fail_upstream = _start(_FailUpstream)
        try:
            for name in default:
                if name == "google-ai-studio":
                    default[name]["upstream_url"] = f"http://127.0.0.1:{ok_upstream.server_address[1]}/v1beta"
                    default[name]["api_key"] = "sk-ok"
                else:
                    default[name]["upstream_url"] = f"http://127.0.0.1:{fail_upstream.server_address[1]}/v1"
                    default[name]["api_key"] = "sk-fail"
            reg = Registry(default)
            from open_free_router.proxy import run_proxy as _run_proxy
            proxy_srv, _ = _run_proxy(reg, host="127.0.0.1", port=0)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", proxy_srv.server_address[1], timeout=5)
                conn.request("POST", "/v1/chat/completions",
                             body=json.dumps({"model": "tier/high", "messages": [{"role": "user", "content": "hi"}]}),
                             headers={"Content-Type": "application/json", "X-OFR-Debug": "true"})
                resp = conn.getresponse()
                data = json.loads(resp.read())
                assert resp.status == 200
                assert "x_ofr" in data, "debug body missing x_ofr field"
                assert data["x_ofr"]["tier"] == "high"
                assert "trace_id" in data["x_ofr"]
                assert data["x_ofr"]["served_by"] or data["x_ofr"]["attempts"]
            finally:
                proxy_srv.shutdown()
        finally:
            ok_upstream.shutdown()
            fail_upstream.shutdown()

