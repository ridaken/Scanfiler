"""Orchestration: walk the inbox, dedupe, extract, call the AI, emit proposals.

One file at a time (per-file reasoning), exactly like actual-ai-categorizer processes
one transaction at a time. Nothing here mutates the filesystem — it produces Proposal
objects; apply.py performs the moves.
"""

from __future__ import annotations

import fnmatch
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .ai import prompt as prompt_mod
from .ai.client import AIClient
from .config import Config
from .extract import classify, extract
from .ledger import STATUS_ERROR, Ledger, LedgerEntry, hash_file
from .naming import (
    normalize_extension,
    resolve_collision,
    sanitize_component,
    sanitize_subdir,
    with_date_prefix,
)
from .proposals import Proposal


@dataclass
class PlanStats:
    proposed: int = 0
    unsorted: int = 0
    skipped_seen: int = 0
    skipped_selection: int = 0
    errors: int = 0


def list_subdirs(library_dir: Path) -> list[str]:
    if not library_dir.is_dir():
        return []
    return sorted(
        p.name for p in library_dir.iterdir() if p.is_dir() and not p.name.startswith(".")
    )


def _selected(path: Path, cfg: Config) -> bool:
    """Apply ignore globs, mtime-age guard, and the process_pattern/process_all rule."""
    name = path.name
    for pat in cfg.selection.ignore_globs:
        if fnmatch.fnmatch(name, pat):
            return False
    if classify(path) == "unknown":
        return False
    try:
        if time.time() - path.stat().st_mtime < cfg.selection.min_mtime_age_s:
            return False  # still being written / synced
    except OSError:
        return False
    if cfg.selection.process_all:
        return True
    return re.search(cfg.selection.process_pattern, name) is not None


def iter_inbox(cfg: Config):
    inbox = cfg.paths.inbox_dir
    for path in sorted(inbox.rglob("*")):
        if path.is_file() and _selected(path, cfg):
            yield path


class _TakenIndex:
    """Tracks claimed 'name.ext' (lowercase) per subdir: existing files + new proposals."""

    def __init__(self, library_dir: Path, unsorted_subdir: str):
        self.library_dir = library_dir
        self.unsorted_subdir = unsorted_subdir
        self._cache: dict[str, set[str]] = {}

    def _for(self, subdir: str) -> set[str]:
        if subdir not in self._cache:
            taken: set[str] = set()
            d = self.library_dir / subdir
            if d.is_dir():
                taken = {p.name.lower() for p in d.iterdir() if p.is_file()}
            self._cache[subdir] = taken
        return self._cache[subdir]

    def claim(self, subdir: str, desired: str, ext: str, policy: str) -> str | None:
        taken = self._for(subdir)
        chosen = resolve_collision(desired, ext, taken, policy)
        if chosen is not None:
            taken.add(chosen.lower())
        return chosen


def plan(cfg: Config, client: AIClient, ledger: Ledger) -> tuple[list[Proposal], PlanStats]:
    stats = PlanStats()
    proposals: list[Proposal] = []
    existing_subdirs = list_subdirs(cfg.paths.library_dir)
    system_prompt = prompt_mod.build_system_prompt(cfg, existing_subdirs)
    taken = _TakenIndex(cfg.paths.library_dir, cfg.paths.unsorted_subdir)

    for path in iter_inbox(cfg):
        file_hash = hash_file(path)
        if ledger.seen(file_hash):
            stats.skipped_seen += 1
            continue

        result = extract(path, cfg.extraction)
        if result.error or not result.has_content:
            stats.errors += 1
            ledger.upsert(
                LedgerEntry(
                    file_hash=file_hash,
                    original_name=path.name,
                    status=STATUS_ERROR,
                    error=result.error or "no extractable content",
                )
            )
            continue

        try:
            user_content = prompt_mod.build_user_content(path.name, result)
            decision = client.decide(
                system_prompt, user_content, existing_subdirs, cfg.naming.allow_new_subdirs
            )
        except Exception as exc:  # noqa: BLE001
            stats.errors += 1
            ledger.upsert(
                LedgerEntry(
                    file_hash=file_hash,
                    original_name=path.name,
                    status=STATUS_ERROR,
                    error=f"ai: {type(exc).__name__}: {exc}",
                )
            )
            continue

        proposal = _build_proposal(cfg, path, file_hash, decision, taken)
        proposals.append(proposal)
        if proposal.unsorted:
            stats.unsorted += 1
        else:
            stats.proposed += 1

        from .ledger import STATUS_PROPOSED, STATUS_UNSORTED

        ledger.upsert(
            LedgerEntry(
                file_hash=file_hash,
                original_name=path.name,
                status=STATUS_UNSORTED if proposal.unsorted else STATUS_PROPOSED,
                decision=decision.model_dump(),
            )
        )

    return proposals, stats


def _build_proposal(cfg, path: Path, file_hash: str, decision, taken: _TakenIndex) -> Proposal:
    ext = normalize_extension(path.suffix)
    base = sanitize_component(decision.filename, max_len=cfg.naming.max_filename_len)
    base = with_date_prefix(base, decision.date, cfg.naming.date_prefix)
    base = sanitize_component(base, max_len=cfg.naming.max_filename_len)

    low_conf = decision.confidence < cfg.categorization.confidence_threshold
    new_dir_blocked = decision.is_new_subdir and not cfg.naming.allow_new_subdirs
    unsorted = low_conf or new_dir_blocked

    subdir = cfg.paths.unsorted_subdir if unsorted else sanitize_subdir(decision.subdir)
    chosen = taken.claim(subdir, base, ext, cfg.apply.on_collision)
    if chosen is None:  # on_collision == skip and it collided -> shunt to unsorted
        unsorted = True
        subdir = cfg.paths.unsorted_subdir
        chosen = taken.claim(subdir, base, ext, "suffix") or f"{base}{ext}"

    return Proposal(
        file_hash=file_hash,
        original_path=str(path),
        subdir=subdir,
        new_filename=chosen,
        is_new_subdir=decision.is_new_subdir,
        confidence=decision.confidence,
        doc_type=decision.doc_type,
        date=decision.date,
        summary=decision.summary,
        tags=decision.tags,
        unsorted=unsorted,
    )
