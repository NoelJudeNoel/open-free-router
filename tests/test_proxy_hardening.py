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

from open_free_router.proxy import run_proxy
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
