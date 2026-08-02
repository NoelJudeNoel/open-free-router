#!/usr/bin/env python3
"""Single-instance guard for the open-free-router daemon.

Prevents two `open-free-router serve` processes from binding the same
proxy/UI ports at once (e.g. one from systemd + one started by hand in
a shell, or a leftover crashed instance). When that happens the loser
dies with a bare `OSError: [Errno 98] Address already in use` and, under
systemd's Restart=on-failure, enters a tight auto-restart loop that
never resolves as long as the winner holds the port -- with no
indication of *what* is wrong.

Two complementary checks, in the order they run:

1. **Port probe** -- connect() to each configured port; success means
   a live listener is already there (TIME_WAIT would be refused, not a
   false positive). Fails fast naming the port. Catches the
   "different config dir, same ports" case the lock can't see.

2. **PID-file + flock exclusive lock** -- the authoritative single-
   instance mechanism. Non-blocking LOCK_EX on a pidfile in data_dir;
   held for the daemon's whole lifetime, auto-released by the OS on
   exit (clean or crash). Stricter than the port probe: also covers
   the start->bind() window and records our PID for diagnosis.

`SO_REUSEADDR` is already on (stdlib HTTPServer base) and helps with
TIME_WAIT residue after a clean restart, but does NOT let two live
listeners share a port -- so it cannot prevent this collision class on
its own, which is why this guard exists.
"""
from __future__ import annotations

import fcntl
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class InstanceAlreadyRunningError(RuntimeError):
    """Another open-free-router instance is already running."""


def _port_in_use(host: str, port: int) -> bool:
    """True if something is already listening on (host, port).

    connect()-based rather than bind()-based: bind() would race with
    the real server we're about to start and can produce false
    positives from TIME_WAIT sockets on some kernels, whereas
    connect() only succeeds against an actual live listener. port=0
    means "let the OS pick" and is never probed.
    """
    if not port:
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()

def acquire_instance_lock(
    data_dir: Path,
    proxy_host: str,
    proxy_port: int,
    ui_host: str,
    ui_port: int,
    pidfile: Optional[Path] = None,
) -> tuple["object", Path]:
    """Probe ports, then take an exclusive pidfile lock.

    Returns (lock_file_object, pidfile_path). The caller must keep the
    file object open for as long as the daemon runs (closing it
    releases the flock); pass it to release_instance_lock() on
    shutdown so the pidfile gets unlinked too.

    Raises InstanceAlreadyRunningError with a human-readable message if
    the lock is already held. On a port collision it exits the current
    process via sys.exit (the only sensible response -- we cannot
    serve, and silently returning would let the caller proceed to bind
    and crash with the same opaque Errno 98 this guard replaces).
    """
    # --- 1. port probe ---------------------------------------------------
    for label, h, p in (("proxy", proxy_host, proxy_port),
                        ("UI", ui_host, ui_port)):
        if _port_in_use(h, p):
            sys.exit(
                f"\n  \u2717 open-free-router: {label} port {p} on {h} is already "
                f"in use.\n    Another instance may be running "
                f"(check: pgrep -af 'open-free-router serve').\n    "
                f"Stop it before starting a new one.\n"
            )

    # --- 2. pidfile + flock ---------------------------------------------
    data_dir.mkdir(parents=True, exist_ok=True)
    pidfile = pidfile or (data_dir / "open-free-router.pid")
    # O_CREAT|O_RDWR, not O_EXCL: the file may persist from a previous
    # crash, but the flock is what actually enforces uniqueness -- a
    # stale pidfile with no live lock is harmless and gets overwritten.
    fd = os.open(str(pidfile), os.O_CREAT | os.O_RDWR, 0o644)
    f = os.fdopen(fd, "r+")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Another process holds the lock. Read its PID for the message.
        f.seek(0)
        existing = f.read().strip()
        try:
            f.close()
        except Exception:
            pass
        pid_hint = ""
        for line in existing.splitlines():
            if line.startswith("pid="):
                pid_hint = f" (pid={line.split('=', 1)[1]})"
        raise InstanceAlreadyRunningError(
            f"Another open-free-router instance is already running{pid_hint}.\n"
            f"  Lock held at: {pidfile}\n"
            f"  If this is stale, remove that file after confirming no "
            f"instance is running."
        )

    # We hold the lock -- record who we are for the next would-be starter.
    f.seek(0)
    f.truncate()
    f.write(
        f"pid={os.getpid()}\n"
        f"proxy={proxy_host}:{proxy_port}\n"
        f"ui={ui_host}:{ui_port}\n"
        f"started={datetime.now(timezone.utc).isoformat()}\n"
    )
    f.flush()
    return f, pidfile


def release_instance_lock(lock_file: object, pidfile: Path) -> None:
    """Release the flock and remove the pidfile. Safe to call on a
    partially-set-up guard (lock_file may be None)."""
    if lock_file is not None:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            lock_file.close()
        except Exception:
            pass
    try:
        pidfile.unlink(missing_ok=True)
    except Exception:
        pass

