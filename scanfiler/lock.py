"""Cross-process lockfile so an overlapping cron/timer fire can't double-process.

Uses atomic O_CREAT|O_EXCL. A stale lock (process no longer alive, on POSIX) is
reclaimed; on Windows a stale lock past a generous age is reclaimed by age.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_STALE_AGE_S = 6 * 60 * 60  # reclaim locks older than this (crash safety net)


class LockHeld(RuntimeError):
    """Raised when another process already holds the lock."""


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            return True  # can't cheaply check; fall back to age-based staleness
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_stale(lock_path: Path) -> bool:
    try:
        content = lock_path.read_text(encoding="utf-8").strip()
        pid = int(content.split()[0]) if content else -1
    except (OSError, ValueError):
        return True
    if not _pid_alive(pid):
        return True
    try:
        age = time.time() - lock_path.stat().st_mtime
    except OSError:
        return False
    return age > _STALE_AGE_S


@contextmanager
def file_lock(lock_path: str | Path) -> Iterator[None]:
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if _is_stale(lock_path):
            try:
                lock_path.unlink()
            except OSError:
                pass
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        else:
            raise LockHeld(f"another scanfiler run holds {lock_path}")
    try:
        os.write(fd, f"{os.getpid()} {int(time.time())}".encode())
        os.close(fd)
        yield
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass
