"""Self-update: check for a newer release and update before a run.

Mirrors the actual-ai-categorizer model. Self-contained and fail-safe: any failure is
logged and the run continues on the current version. If an update is applied and
`restart` is set, the process re-execs into the new code so this very run uses it.

Only works when running from a git working tree (the recommended deploy is a git
clone); a wheel install in site-packages is skipped with a warning. Dependencies are
injected (run/reexec/env/log) so the logic is unit-testable without real git.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from .config import AutoUpdateConfig

# Set on the re-exec'd child so it doesn't immediately try to update again (which would
# loop). Cleared after the first cycle so later loop iterations still check for updates.
SENTINEL = "SCANFILER_SELF_UPDATED"


@dataclass
class SelfUpdateDeps:
    repo_dir: str
    run: Callable[[str, list[str]], str]  # run in repo dir, return stdout, raise on error
    reexec: Callable[[], NoReturn]        # re-exec current process; never returns
    env: dict
    log: Callable[[str, str], None]       # (level, message)


@dataclass
class _Target:
    label: str
    commit: str
    checkout: Callable[[], None]


def perform_self_update(cfg: AutoUpdateConfig, deps: SelfUpdateDeps) -> None:
    if not cfg.enabled:
        return

    if deps.env.get(SENTINEL) == "1":
        deps.log("debug", "self-update: running freshly updated version; skipping check")
        deps.env.pop(SENTINEL, None)
        return

    updated = False
    try:
        if not _is_git_repo(deps):
            deps.log("warn", f"self-update: {deps.repo_dir} is not a git repository; skipping")
            return

        deps.run("git", ["fetch", "--tags", "--prune", "--quiet", "origin"])
        old_head = _git(deps, ["rev-parse", "HEAD"])
        target = _resolve_target(cfg, deps)
        if target is None:
            return  # _resolve_target already logged why

        if target.commit == old_head:
            deps.log("info", f"self-update: already up to date ({target.label})")
            return
        if not _is_clean_tree(deps):
            deps.log("warn", "self-update: working tree has uncommitted changes; skipping")
            return
        if cfg.verify_signature and not _verify_commit(deps, cfg, target.commit):
            deps.log(
                "error",
                f"self-update: signature verification FAILED for {target.label} "
                f"({target.commit[:8]}); refusing to update",
            )
            return

        deps.log("info", f"self-update: updating to {target.label}")
        target.checkout()
        updated = True

        try:
            if cfg.install_deps and _pyproject_changed(deps, old_head, target.commit):
                deps.log("info", "self-update: dependencies changed; reinstalling")
                deps.run(sys.executable, ["-m", "pip", "install", "-e", ".", "--quiet"])
        except Exception as post_err:  # noqa: BLE001
            deps.log(
                "error",
                f"self-update: post-update step failed ({post_err}); "
                f"rolling back to {old_head[:8]}",
            )
            _rollback(deps, old_head)
            return
    except Exception as err:  # noqa: BLE001
        deps.log("warn", f"self-update: check failed ({err}); continuing with current version")
        return

    if updated and cfg.restart:
        deps.log("info", "self-update: restarting into the updated version")
        deps.reexec()  # never returns


def _git(deps: SelfUpdateDeps, args: list[str]) -> str:
    return deps.run("git", args).strip()


def _is_git_repo(deps: SelfUpdateDeps) -> bool:
    try:
        return _git(deps, ["rev-parse", "--is-inside-work-tree"]) == "true"
    except Exception:  # noqa: BLE001
        return False


def _is_clean_tree(deps: SelfUpdateDeps) -> bool:
    return _git(deps, ["status", "--porcelain"]) == ""


def _verify_commit(deps: SelfUpdateDeps, cfg: AutoUpdateConfig, commit: str) -> bool:
    args: list[str] = []
    if cfg.allowed_signers_file:
        args += ["-c", "gpg.format=ssh",
                 "-c", f"gpg.ssh.allowedSignersFile={cfg.allowed_signers_file}"]
    args += ["verify-commit", "--raw", commit]
    try:
        deps.run("git", args)
        deps.log("info", f"self-update: signature verified for {commit[:8]}")
        return True
    except Exception:  # noqa: BLE001
        return False


def _pyproject_changed(deps: SelfUpdateDeps, frm: str, to: str) -> bool:
    try:
        return _git(deps, ["diff", "--name-only", frm, to, "--", "pyproject.toml"]) != ""
    except Exception:  # noqa: BLE001
        return True  # can't tell -> reinstall to be safe


def _resolve_target(cfg: AutoUpdateConfig, deps: SelfUpdateDeps) -> _Target | None:
    if cfg.ref == "latest-release":
        tags = [
            t.strip()
            for t in _git(deps, ["tag", "-l", "v*.*.*", "--sort=-v:refname"]).splitlines()
            if t.strip()
        ]
        if not tags:
            deps.log("info", "self-update: no release tags found; skipping")
            return None
        tag = tags[0]
        return _Target(
            label=tag,
            commit=_git(deps, ["rev-parse", f"{tag}^{{commit}}"]),
            checkout=lambda: deps.run(
                "git", ["-c", "advice.detachedHead=false", "checkout", "--quiet", tag]
            ),
        )

    branch = cfg.ref

    def _checkout_branch() -> None:
        deps.run("git", ["checkout", "--quiet", branch])
        deps.run("git", ["merge", "--ff-only", "--quiet", f"origin/{branch}"])

    return _Target(
        label=branch,
        commit=_git(deps, ["rev-parse", f"origin/{branch}"]),
        checkout=_checkout_branch,
    )


def _rollback(deps: SelfUpdateDeps, old_head: str) -> None:
    try:
        deps.run("git", ["-c", "advice.detachedHead=false", "checkout", "--quiet", old_head])
    except Exception:  # noqa: BLE001
        pass  # nothing more we can do; current in-memory code still runs this cycle


# ---- default (production) dependency implementations ----

def resolve_repo_dir(override: str | None = None) -> str:
    """Repo root: explicit override, else the directory above this package."""
    if override:
        return override
    return str(Path(__file__).resolve().parent.parent)


def default_run(repo_dir: str) -> Callable[[str, list[str]], str]:
    def _run(cmd: str, args: list[str]) -> str:
        return subprocess.run(
            [cmd, *args],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    return _run


def default_reexec(env: dict) -> NoReturn:
    """Re-run `python -m scanfiler <same args>` with the sentinel set, then exit."""
    child_env = {**env, SENTINEL: "1"}
    argv = [sys.executable, "-m", "scanfiler", *sys.argv[1:]]
    result = subprocess.run(argv, env=child_env)
    sys.exit(result.returncode)


def _default_log(level: str, message: str) -> None:
    stream = sys.stderr if level in ("warn", "error") else sys.stdout
    print(message, file=stream)


def default_deps(cfg: AutoUpdateConfig) -> SelfUpdateDeps:
    repo_dir = resolve_repo_dir(cfg.repo_dir)
    return SelfUpdateDeps(
        repo_dir=repo_dir,
        run=default_run(repo_dir),
        reexec=lambda: default_reexec(dict(os.environ)),
        env=os.environ,
        log=_default_log,
    )
