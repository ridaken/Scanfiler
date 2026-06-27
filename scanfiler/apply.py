"""Execute proposals (copy/move into the library), audit every action, and undo.

The audit log is append-only JSONL; each row is reversible. `undo` reads it back and
restores the most recent run (or a named run). RAG sidecars are written next to the
organized file when enabled.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .ledger import (
    STATUS_APPLIED,
    STATUS_UNSORTED,
    Ledger,
    LedgerEntry,
)
from .proposals import Proposal


@dataclass
class ApplyResult:
    run_id: str
    applied: int = 0
    skipped: int = 0
    errors: int = 0


def _audit(audit_file: Path, record: dict) -> None:
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    with open(audit_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _dest_for(cfg: Config, p: Proposal) -> Path:
    subdir = cfg.paths.unsorted_subdir if p.unsorted else p.subdir
    return cfg.paths.library_dir / subdir / p.new_filename


def _write_sidecar(dest: Path, p: Proposal) -> Path | None:
    sidecar = dest.with_suffix(dest.suffix + ".json")
    payload = {
        "doc_type": p.doc_type,
        "date": p.date,
        "summary": p.summary,
        "tags": p.tags,
        "confidence": p.confidence,
        "source_hash": p.file_hash,
    }
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return sidecar


def apply_proposals(
    cfg: Config,
    proposals: list[Proposal],
    ledger: Ledger,
    *,
    dry_run: bool = False,
    run_id: str | None = None,
) -> ApplyResult:
    run_id = run_id or uuid.uuid4().hex[:12]
    result = ApplyResult(run_id=run_id)

    for p in proposals:
        src = Path(p.original_path)
        dest = _dest_for(cfg, p)
        try:
            if not src.is_file():
                result.skipped += 1
                continue
            if dest.exists() and cfg.apply.on_collision == "skip":
                result.skipped += 1
                continue

            if dry_run:
                result.applied += 1
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            if cfg.apply.action == "move":
                shutil.move(str(src), str(dest))
            else:
                shutil.copy2(str(src), str(dest))

            sidecar = None
            if cfg.rag.write_sidecar:
                sidecar = _write_sidecar(dest, p)

            _audit(
                cfg.logging.audit_file,
                {
                    "run_id": run_id,
                    "ts": time.time(),
                    "action": cfg.apply.action,
                    "file_hash": p.file_hash,
                    "src": str(src),
                    "dst": str(dest),
                    "sidecar": str(sidecar) if sidecar else None,
                },
            )
            ledger.upsert(
                LedgerEntry(
                    file_hash=p.file_hash,
                    original_name=src.name,
                    status=STATUS_UNSORTED if p.unsorted else STATUS_APPLIED,
                    decision={"subdir": p.subdir, "new_filename": p.new_filename},
                    metadata={
                        "doc_type": p.doc_type,
                        "date": p.date,
                        "summary": p.summary,
                        "tags": p.tags,
                        "confidence": p.confidence,
                    },
                    new_path=str(dest),
                )
            )
            result.applied += 1
        except Exception as exc:  # noqa: BLE001 — keep the batch going, record the failure
            result.errors += 1
            _audit(
                cfg.logging.audit_file,
                {
                    "run_id": run_id,
                    "ts": time.time(),
                    "action": "error",
                    "file_hash": p.file_hash,
                    "src": str(src),
                    "dst": str(dest),
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
    return result


def undo(cfg: Config, *, run_id: str | None = None, last: bool = False) -> int:
    """Reverse a run from the audit log. Returns the number of files restored."""
    audit_file = cfg.logging.audit_file
    if not audit_file.is_file():
        return 0

    lines = audit_file.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    moves = [r for r in records if r.get("action") in ("copy", "move")]
    if not moves:
        return 0

    if last and not run_id:
        run_id = moves[-1]["run_id"]
    target = [r for r in moves if r["run_id"] == run_id]
    if not target:
        return 0

    restored = 0
    for r in reversed(target):  # undo in reverse order
        dst = Path(r["dst"])
        src = Path(r["src"])
        try:
            if r["action"] == "move":
                if dst.exists():
                    src.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dst), str(src))
            else:  # copy: just remove the library copy
                if dst.exists():
                    dst.unlink()
            sidecar = r.get("sidecar")
            if sidecar and Path(sidecar).exists():
                Path(sidecar).unlink()
            restored += 1
        except OSError:
            continue
    return restored
