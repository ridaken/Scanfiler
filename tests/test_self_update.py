"""Self-update logic tests with a fake git/command runner (no real git or network)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from scanfiler.config import AutoUpdateConfig
from scanfiler.self_update import (
    SENTINEL,
    SelfUpdateDeps,
    default_deps,
    default_reexec,
    default_run,
    perform_self_update,
    resolve_repo_dir,
)


class FakeGit:
    """Scriptable stand-in for the injected `run`; records every invocation."""

    def __init__(
        self,
        *,
        is_repo=True,
        head="oldsha",
        tags=("v0.2.0",),
        tag_commit="newsha",
        origin_commit="newsha",
        clean=True,
        verify_ok=True,
        pyproject_changed=True,
        fetch_raises=False,
        fail=(),  # substrings; any command whose args contain one raises
    ):
        self.is_repo = is_repo
        self.head = head
        self.tags = list(tags)
        self.tag_commit = tag_commit
        self.origin_commit = origin_commit
        self.clean = clean
        self.verify_ok = verify_ok
        self.pyproject_changed = pyproject_changed
        self.fetch_raises = fetch_raises
        self.fail = fail
        self.calls: list[tuple[str, list[str]]] = []

    def run(self, cmd: str, args: list[str]) -> str:
        self.calls.append((cmd, args))
        if any(f in args for f in self.fail):
            raise RuntimeError(f"command failed: {args}")

        if cmd != "git":  # e.g. pip install
            return ""
        if "--is-inside-work-tree" in args:
            if not self.is_repo:
                raise RuntimeError("not a repo")
            return "true\n"
        if "fetch" in args:
            if self.fetch_raises:
                raise RuntimeError("network down")
            return ""
        if args[:2] == ["rev-parse", "HEAD"]:
            return self.head + "\n"
        if "rev-parse" in args and any(a.endswith("^{commit}") for a in args):
            return self.tag_commit + "\n"
        if "rev-parse" in args and any(a.startswith("origin/") for a in args):
            return self.origin_commit + "\n"
        if "tag" in args:
            return "\n".join(self.tags) + "\n"
        if "status" in args:
            return "" if self.clean else " M file\n"
        if "verify-commit" in args:
            if not self.verify_ok:
                raise RuntimeError("bad signature")
            return ""
        if "diff" in args:
            return "pyproject.toml\n" if self.pyproject_changed else ""
        if "checkout" in args or "merge" in args:
            return ""
        return ""


def _deps(fake: FakeGit, env=None):
    state = {"reexec": False}

    def reexec():
        state["reexec"] = True

    logs: list[tuple[str, str]] = []
    deps = SelfUpdateDeps(
        repo_dir="/repo",
        run=fake.run,
        reexec=reexec,
        env=env if env is not None else {},
        log=lambda level, msg: logs.append((level, msg)),
    )
    return deps, state, logs


def _did(fake, verb):
    return any(verb in args for _, args in fake.calls)


def test_disabled_does_nothing():
    fake = FakeGit()
    deps, state, _ = _deps(fake)
    perform_self_update(AutoUpdateConfig(enabled=False), deps)
    assert fake.calls == []
    assert state["reexec"] is False


def test_sentinel_skips_and_clears():
    fake = FakeGit()
    env = {SENTINEL: "1"}
    deps, state, _ = _deps(fake, env=env)
    perform_self_update(AutoUpdateConfig(enabled=True), deps)
    assert fake.calls == []  # no git work
    assert SENTINEL not in env  # cleared for next cycle


def test_not_a_repo_skips():
    fake = FakeGit(is_repo=False)
    deps, state, logs = _deps(fake)
    perform_self_update(AutoUpdateConfig(enabled=True), deps)
    assert not _did(fake, "fetch")
    assert any(lvl == "warn" for lvl, _ in logs)


def test_up_to_date_no_checkout():
    fake = FakeGit(head="samesha", tag_commit="samesha")
    deps, state, logs = _deps(fake)
    perform_self_update(AutoUpdateConfig(enabled=True, verify_signature=False), deps)
    assert not _did(fake, "checkout")
    assert state["reexec"] is False


def test_dirty_tree_skips():
    fake = FakeGit(clean=False)
    deps, state, logs = _deps(fake)
    perform_self_update(AutoUpdateConfig(enabled=True, verify_signature=False), deps)
    assert not _did(fake, "checkout")


def test_signature_failure_refuses():
    fake = FakeGit(verify_ok=False)
    deps, state, logs = _deps(fake)
    perform_self_update(AutoUpdateConfig(enabled=True, verify_signature=True), deps)
    assert not _did(fake, "checkout")
    assert any(lvl == "error" for lvl, _ in logs)


def test_successful_update_reexecs_and_reinstalls():
    fake = FakeGit(pyproject_changed=True)
    deps, state, logs = _deps(fake)
    perform_self_update(AutoUpdateConfig(enabled=True, verify_signature=False), deps)
    assert _did(fake, "checkout")
    assert any(cmd == sys.executable for cmd, _ in fake.calls)  # pip install ran
    assert state["reexec"] is True


def test_update_skips_reinstall_when_pyproject_unchanged():
    fake = FakeGit(pyproject_changed=False)
    deps, state, _ = _deps(fake)
    perform_self_update(AutoUpdateConfig(enabled=True, verify_signature=False), deps)
    assert not any(cmd == sys.executable for cmd, _ in fake.calls)
    assert state["reexec"] is True


def test_restart_false_no_reexec():
    fake = FakeGit()
    deps, state, _ = _deps(fake)
    perform_self_update(
        AutoUpdateConfig(enabled=True, verify_signature=False, restart=False), deps
    )
    assert _did(fake, "checkout")
    assert state["reexec"] is False


def test_post_step_failure_rolls_back():
    # pip install fails -> rollback checks out the old HEAD, no reexec.
    fake = FakeGit(pyproject_changed=True, fail=("install",))
    deps, state, logs = _deps(fake)
    perform_self_update(AutoUpdateConfig(enabled=True, verify_signature=False), deps)
    assert state["reexec"] is False
    assert any("oldsha" in args for _, args in fake.calls)  # rollback checkout
    assert any(lvl == "error" for lvl, _ in logs)


def test_branch_ref_uses_origin_and_merges():
    fake = FakeGit(origin_commit="branchsha")
    deps, state, _ = _deps(fake)
    perform_self_update(
        AutoUpdateConfig(enabled=True, verify_signature=False, ref="main"), deps
    )
    assert _did(fake, "merge")
    assert state["reexec"] is True


def test_no_tags_skips():
    fake = FakeGit(tags=())
    deps, state, logs = _deps(fake)
    perform_self_update(AutoUpdateConfig(enabled=True, verify_signature=False), deps)
    assert not _did(fake, "checkout")
    assert any("no release tags" in msg for _, msg in logs)


def test_fetch_error_continues():
    fake = FakeGit(fetch_raises=True)
    deps, state, logs = _deps(fake)
    perform_self_update(AutoUpdateConfig(enabled=True), deps)
    assert state["reexec"] is False
    assert any(lvl == "warn" for lvl, _ in logs)


def test_allowed_signers_file_passed_to_verify():
    fake = FakeGit()
    deps, state, _ = _deps(fake)
    perform_self_update(
        AutoUpdateConfig(enabled=True, verify_signature=True,
                         allowed_signers_file="/etc/scanfiler/allowed_signers"),
        deps,
    )
    verify_call = next(args for cmd, args in fake.calls if "verify-commit" in args)
    assert "gpg.format=ssh" in verify_call
    assert any("allowedSignersFile=/etc/scanfiler/allowed_signers" in a for a in verify_call)


# ---- default helpers ----

def test_resolve_repo_dir_override_and_default():
    assert resolve_repo_dir("/custom") == "/custom"
    default = resolve_repo_dir()
    assert default.endswith("scanfiler") or "scanfiler" in default


def test_default_run_executes(tmp_path):
    run = default_run(str(tmp_path))
    out = run(sys.executable, ["-c", "print('hello-run')"])
    assert "hello-run" in out


def test_default_run_raises_on_failure(tmp_path):
    run = default_run(str(tmp_path))
    with pytest.raises(subprocess.CalledProcessError):
        run(sys.executable, ["-c", "import sys; sys.exit(3)"])


def test_default_reexec_sets_sentinel_and_exits(monkeypatch):
    captured = {}

    class _Res:
        returncode = 0

    def fake_run(argv, env=None):
        captured["argv"] = argv
        captured["env"] = env
        return _Res()

    monkeypatch.setattr("scanfiler.self_update.subprocess.run", fake_run)
    monkeypatch.setattr(sys, "argv", ["scanfiler", "run", "-c", "config.yaml"])
    with pytest.raises(SystemExit):
        default_reexec({"PATH": "x"})
    assert captured["argv"][:3] == [sys.executable, "-m", "scanfiler"]
    assert captured["argv"][3:] == ["run", "-c", "config.yaml"]
    assert captured["env"][SENTINEL] == "1"


def test_default_deps_wired():
    deps = default_deps(AutoUpdateConfig(repo_dir="/repo"))
    assert deps.repo_dir == "/repo"
    assert callable(deps.run) and callable(deps.reexec)
