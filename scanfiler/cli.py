"""Command-line interface: init | plan | apply | run | loop | undo | status."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__
from .config import Config, load_config
from .templates import DEFAULT_CONFIG

DEFAULT_CONFIG_PATH = "config.yaml"
DEFAULT_PROPOSALS_PATH = "proposals.jsonl"


def _load(args) -> Config:
    return load_config(args.config)


def _make_client(cfg: Config):
    from .ai.client import make_client

    return make_client(cfg.ai)


def _stats_line(stats) -> str:
    return (
        f"sent={stats.sent} proposed={stats.proposed} unsorted={stats.unsorted} "
        f"skipped_seen={stats.skipped_seen} "
        f"skipped_criteria={stats.skipped_selection + stats.skipped_no_content} "
        f"errors={stats.errors}"
    )


def cmd_init(args) -> int:
    dest = Path(args.config)
    if dest.exists():
        print(f"{dest} already exists — not overwriting.")
        return 1
    dest.write_text(DEFAULT_CONFIG, encoding="utf-8")
    print(f"Wrote {dest}. Edit paths.inbox_dir / library_dir and ai.* before running.")
    return 0


def cmd_plan(args) -> int:
    from .ledger import open_ledger
    from .pipeline import plan
    from .proposals import write_proposals
    from .runlog import open_run_log

    cfg = _load(args)
    client = _make_client(cfg)
    with open_run_log(cfg) as (log, log_path), open_ledger(cfg.logging.ledger_db) as ledger:
        proposals, stats = plan(cfg, client, ledger, log)
    if not args.dry_run:
        write_proposals(args.proposals, proposals)
        print(f"Wrote {len(proposals)} proposals to {args.proposals}")
    else:
        print("[dry-run] not writing proposals file")
    print(_stats_line(stats))
    if log_path:
        print(f"log: {log_path}")
    return 0


def cmd_apply(args) -> int:
    from .apply import apply_proposals
    from .ledger import open_ledger
    from .proposals import read_proposals

    cfg = _load(args)
    proposals = list(read_proposals(args.proposals))
    with open_ledger(cfg.logging.ledger_db) as ledger:
        result = apply_proposals(cfg, proposals, ledger, dry_run=args.dry_run)
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}run={result.run_id} applied={result.applied} "
          f"skipped={result.skipped} errors={result.errors}")
    return 0


def _run_once(cfg: Config, args) -> int:
    from .apply import apply_proposals
    from .ledger import open_ledger
    from .lock import LockHeld, file_lock
    from .pipeline import plan
    from .proposals import write_proposals
    from .runlog import open_run_log

    # Check for + apply a newer release before doing any work. Done before the lock so a
    # re-exec into the updated version doesn't deadlock against our own lockfile. On a
    # dry run we never mutate the install.
    if cfg.auto_update.enabled and not args.dry_run:
        from .self_update import default_deps, perform_self_update

        perform_self_update(cfg.auto_update, default_deps(cfg.auto_update))

    client = _make_client(cfg)
    lock_path = Path(cfg.logging.ledger_db).with_suffix(".lock")
    try:
        with file_lock(lock_path):
            with open_run_log(cfg) as (log, log_path), open_ledger(cfg.logging.ledger_db) as ledger:
                proposals, stats = plan(cfg, client, ledger, log)
                print(_stats_line(stats))
                if log_path:
                    print(f"log: {log_path}")
                if cfg.apply.mode == "auto" and not args.dry_run:
                    result = apply_proposals(cfg, proposals, ledger)
                    print(f"[auto-apply] run={result.run_id} applied={result.applied} "
                          f"skipped={result.skipped} errors={result.errors}")
                else:
                    if not args.dry_run:
                        write_proposals(args.proposals, proposals)
                        print(f"Wrote {len(proposals)} proposals to {args.proposals} "
                              f"(review, then `scanfiler apply`)")
    except LockHeld as exc:
        print(f"skip: {exc}")
        return 0
    return 0


def cmd_run(args) -> int:
    return _run_once(_load(args), args)


def cmd_loop(args) -> int:
    cfg = _load(args)
    interval = cfg.scheduler.polling_minutes * 60
    print(f"loop: every {cfg.scheduler.polling_minutes} min (Ctrl-C to stop)")
    while True:
        try:
            _run_once(cfg, args)
        except KeyboardInterrupt:
            print("\nstopped.")
            return 0
        except Exception as exc:  # noqa: BLE001 — a daemon should survive a bad cycle
            print(f"cycle error: {type(exc).__name__}: {exc}", file=sys.stderr)
        time.sleep(interval)


def cmd_undo(args) -> int:
    from .apply import undo

    cfg = _load(args)
    n = undo(cfg, run_id=args.run, last=args.last or not args.run)
    print(f"restored {n} files")
    return 0


def cmd_status(args) -> int:
    from .ledger import open_ledger

    cfg = _load(args)
    with open_ledger(cfg.logging.ledger_db) as ledger:
        counts = ledger.counts()
    if not counts:
        print("ledger empty.")
        return 0
    for status, n in sorted(counts.items()):
        print(f"{status:>10}: {n}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scanfiler", description=__doc__)
    p.add_argument("--version", action="version", version=f"scanfiler {__version__}")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG_PATH, help="path to config.yaml")
    sub = p.add_subparsers(dest="command", required=True)

    def add_proposals(sp):
        sp.add_argument("--proposals", default=DEFAULT_PROPOSALS_PATH,
                        help="proposals JSONL path")

    def add_dry(sp):
        sp.add_argument("--dry-run", action="store_true",
                        help="decide + log but never touch disk")

    sp = sub.add_parser("init", help="scaffold a config.yaml")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("plan", help="extract + AI, write proposals (no moves)")
    add_proposals(sp)
    add_dry(sp)
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("apply", help="execute proposals into the library")
    add_proposals(sp)
    add_dry(sp)
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser("run", help="one full cycle (plan, then apply if mode=auto)")
    add_proposals(sp)
    add_dry(sp)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("loop", help="daemon: run a cycle every polling_minutes")
    add_proposals(sp)
    add_dry(sp)
    sp.set_defaults(func=cmd_loop)

    sp = sub.add_parser("undo", help="reverse a run from the audit log")
    sp.add_argument("--run", help="run id to undo")
    sp.add_argument("--last", action="store_true", help="undo the most recent run")
    sp.set_defaults(func=cmd_undo)

    sp = sub.add_parser("status", help="show ledger counts")
    sp.set_defaults(func=cmd_status)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
