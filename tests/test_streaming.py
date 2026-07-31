"""End-to-end tests for stream:true chat completions.

These spin up a real fake-upstream HTTP server plus a real proxy server
(both in background threads) and drive them with real socket connections,
to catch what a mocked urlopen would hide: whether chunks actually arrive
incrementally, and whether error status codes before any streaming starts
still come back as clean JSON.

proxy.py always POSTs to ``f"{upstream_url.rstrip('/')}/chat/completions"``,
so each fake upstream below only needs to implement that one path — a
separate handler class per scenario, on its own port, keeps that simple.
"""
from __future__ import annotations

import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from open_free_router.proxy import run_proxy
from open_free_router.registry import Registry


class _StreamingUpstreamHandler(BaseHTTPRequestHandler):
    """Fake upstream that streams 3 SSE chunks with a delay between each."""

    disable_nagle_algorithm = True  # avoid Nagle+delayed-ACK masking real streaming

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        chunks = [
            b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
            b'data: [DONE]\n\n',
        ]
        for c in chunks:
            self.wfile.write(f"{len(c):x}\r\n".encode())
            self.wfile.write(c)
            self.wfile.write(b"\r\n")
            self.wfile.flush()
            time.sleep(0.02)  # force separate TCP writes, not one buffered blob
        self.wfile.write(b"0\r\n\r\n")


class _ErrorBeforeStreamUpstreamHandler(BaseHTTPRequestHandler):
    """Fake upstream that fails before sending any body (e.g. rate limit)."""

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({"error": {"message": "rate limited"}}).encode()
        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def _proxy_for(upstream_port: int):
    reg = Registry({
        "fake": {
            "upstream_url": f"http://127.0.0.1:{upstream_port}/v1",
            "api_key": "sk-test",
            "models": [{"id": "stream-model"}],
        },
    })
    return run_proxy(reg, host="127.0.0.1", port=0)


def test_stream_true_relays_chunks_incrementally():
    """The client must see the first chunk well before the last one is
    sent — i.e. the proxy is not buffering the full response before
    replying, which is what a plain urlopen()+one wfile.write() would do."""
    upstream = _start(_StreamingUpstreamHandler)
    proxy_srv, _ = _proxy_for(upstream.server_address[1])
    try:
        conn = http.client.HTTPConnection("127.0.0.1", proxy_srv.server_address[1], timeout=5)
        conn.request("POST", "/v1/chat/completions", body=json.dumps({
            "model": "stream-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 200
        assert resp.getheader("Transfer-Encoding") == "chunked"

        t0 = time.monotonic()
        first_byte_time = None
        body = b""
        while True:
            chunk = resp.read(1)
            if not chunk:
                break
            if first_byte_time is None:
                first_byte_time = time.monotonic()
            body += chunk
        total_time = time.monotonic() - t0

        assert b"Hel" in body
        assert b"[DONE]" in body
        assert first_byte_time is not None
        # Fake upstream sleeps 20ms between each of 3 chunks (~60ms total
        # end-to-end). If the proxy buffered everything before responding,
        # first_byte_time would land near total_time; streamed, it lands
        # near the start.
        assert (first_byte_time - t0) < (total_time * 0.6)
        conn.close()
    finally:
        proxy_srv.shutdown()
        upstream.shutdown()


def test_stream_error_before_body_returns_clean_json():
    upstream = _start(_ErrorBeforeStreamUpstreamHandler)
    proxy_srv, _ = _proxy_for(upstream.server_address[1])
    try:
        conn = http.client.HTTPConnection("127.0.0.1", proxy_srv.server_address[1], timeout=5)
        conn.request("POST", "/v1/chat/completions", body=json.dumps({
            "model": "stream-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 429
        data = json.loads(resp.read())
        assert data["error"]["message"] == "rate limited"
        conn.close()
    finally:
        proxy_srv.shutdown()
        upstream.shutdown()
