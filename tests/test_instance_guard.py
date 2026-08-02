"""Tests for the single-instance guard (_instance_guard.py).

Covers the failure mode this guard exists to prevent: two
`open-free-router serve` processes racing for the same ports. Tests
target the guard module directly (not the full Daemon) so they're fast
and never touch a real pidfile path.
"""
from __future__ import annotations

import socket
import sys

import pytest

from open_free_router._instance_guard import (
    acquire_instance_lock,
    release_instance_lock,
    InstanceAlreadyRunningError,
    _port_in_use,
)


def _free_listening_socket():
    """A socket bound to an OS-chosen free port, actively listening.
    Used to simulate 'something is already on this port' for the port
    probe without racing with the real daemon's ports."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s


class TestPortProbe:
    def test_free_port_not_in_use(self):
        # Pick a port then close it -- nothing listening -> not in use.
        s = _free_listening_socket()
        port = s.getsockname()[1]
        s.close()
        assert _port_in_use("127.0.0.1", port) is False

    def test_live_listener_detected(self):
        s = _free_listening_socket()
        port = s.getsockname()[1]
        try:
            assert _port_in_use("127.0.0.1", port) is True
        finally:
            s.close()

    def test_port_zero_is_not_probed(self):
        # port=0 means "let the OS pick"; must never be reported as in
        # use, or the guard would false-positive on every test/dev run
        # that uses port=0.
        assert _port_in_use("127.0.0.1", 0) is False


class TestPidfileLock:
    def test_first_acquire_succeeds_and_writes_pid(self, tmp_path):
        lf, pidfile = acquire_instance_lock(
            tmp_path, "127.0.0.1", 0, "127.0.0.1", 0)
        try:
            assert pidfile.exists()
            content = pidfile.read_text()
            assert f"pid={__import__('os').getpid()}" in content
        finally:
            release_instance_lock(lf, pidfile)

    def test_second_acquire_raises(self, tmp_path):
        lf1, pidfile = acquire_instance_lock(
            tmp_path, "127.0.0.1", 0, "127.0.0.1", 0)
        try:
            with pytest.raises(InstanceAlreadyRunningError) as exc:
                acquire_instance_lock(
                    tmp_path, "127.0.0.1", 0, "127.0.0.1", 0,
                    pidfile=pidfile)
            # message should point at the pidfile for diagnosis
            assert str(pidfile) in str(exc.value)
        finally:
            release_instance_lock(lf1, pidfile)

    def test_lock_released_allows_reacquire(self, tmp_path):
        lf1, pidfile = acquire_instance_lock(
            tmp_path, "127.0.0.1", 0, "127.0.0.1", 0)
        release_instance_lock(lf1, pidfile)
        # After release, a new acquire must succeed and the stale
        # pidfile must have been cleaned up (then recreated by the new
        # owner) -- i.e. no lingering lock blocking the second start.
        lf2, pidfile2 = acquire_instance_lock(
            tmp_path, "127.0.0.1", 0, "127.0.0.1", 0, pidfile=pidfile)
        try:
            assert pidfile2 == pidfile
            assert pidfile.exists()
        finally:
            release_instance_lock(lf2, pidfile2)

    def test_release_is_idempotent_on_partial_setup(self, tmp_path):
        # Safe to call with None lock (e.g. if acquire failed mid-way);
        # must not raise, and must not crash on a missing pidfile.
        release_instance_lock(None, tmp_path / "does-not-exist.pid")


class TestPortCollisionExits:
    def test_proxy_port_collision_exits_process(self, tmp_path):
        s = _free_listening_socket()
        port = s.getsockname()[1]
        try:
            with pytest.raises(SystemExit) as exc:
                acquire_instance_lock(
                    tmp_path, "127.0.0.1", port, "127.0.0.1", 0)
            # exit message names the port so the user knows what's wrong
            assert str(port) in str(exc.value)
        finally:
            s.close()
