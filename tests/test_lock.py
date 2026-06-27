import os
import sys
import time

import pytest

from scanfiler.lock import _STALE_AGE_S, LockHeld, file_lock


def test_lock_acquire_release(tmp_path):
    lp = tmp_path / "x.lock"
    with file_lock(lp):
        assert lp.exists()
    assert not lp.exists()  # released on exit


def test_lock_held_raises(tmp_path):
    lp = tmp_path / "x.lock"
    with file_lock(lp):
        with pytest.raises(LockHeld):
            with file_lock(lp):
                pass


def test_stale_lock_reclaimed_by_age(tmp_path):
    # An old lock is reclaimed by age on every platform (crash safety net).
    lp = tmp_path / "x.lock"
    lp.write_text(f"{os.getpid()} 0", encoding="utf-8")
    old = time.time() - _STALE_AGE_S - 60
    os.utime(lp, (old, old))
    with file_lock(lp):
        assert lp.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="PID liveness check is POSIX-only")
def test_stale_lock_reclaimed_by_dead_pid(tmp_path):
    lp = tmp_path / "x.lock"
    lp.write_text("999999999 0", encoding="utf-8")  # definitely-dead PID
    with file_lock(lp):
        assert lp.exists()


def test_garbage_lock_reclaimed(tmp_path):
    lp = tmp_path / "x.lock"
    lp.write_text("not-a-pid", encoding="utf-8")
    with file_lock(lp):
        assert lp.exists()


def test_lock_writes_pid(tmp_path):
    lp = tmp_path / "x.lock"
    with file_lock(lp):
        content = lp.read_text(encoding="utf-8")
        assert content.split()[0] == str(os.getpid())
