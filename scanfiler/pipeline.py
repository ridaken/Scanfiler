"""Orchestration: walk the inbox, dedupe, extract, call the AI, emit proposals.

One file at a time (per-file reasoning), exactly like actual-ai-categorizer processes
one transaction at a time. Nothing here mutates the filesystem — it produces Proposal
objects; apply.py performs the moves.
"""

from __future__ import annotations

import fnmatch
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .ai import prompt as prompt_mod
from .ai.client import AIClient, AIError
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

_NULL_LOG = logging.getLogger("scanfiler.pipeline.null")
_NULL_LOG.addHandler(logging.NullHandler())


@dataclass
class PlanStats:
    sent: int = 0                 # files for which an LLM request was attempted
    proposed: int = 0
    unsorted: int = 0
    skipped_seen: int = 0         # already judged by the AI on a prior run
    skipped_selection: int = 0    # didn't fit run criteria (already named, ignored, too new)
    skipped_no_content: int = 0   # nothing to send in this mode (e.g. image under text mode)
    errors: int = 0


def list_subdirs(library_dir: Path) -> list[str]:
    if not library_dir.is_dir():
        return []
    return sorted(
        p.name for p in library_dir.iterdir() if p.is_dir() and not p.name.startswith(".")
    )


def _select_reason(path: Path, cfg: Config) -> str | None:
    """Why this file is NOT eligible to be sent, or None if it is.

    Covers ignore globs, unsupported types, the mtime-age guard, and the
    process_pattern/process_all rule (already-named files).
    """
    name = path.name
    for pat in cfg.selection.ignore_globs:
        if fnmatch.fnmatch(name, pat):
            return f"ignored (matches glob {pat!r})"
    if classify(path) == "unknown":
        return "unsupported file type"
    try:
        if time.time() - path.stat().st_mtime < cfg.selection.min_mtime_age_s:
            return "too recently modified (still being written/synced)"
    except OSError:
        return "stat failed"
    if not cfg.selection.process_all and re.search(cfg.selection.process_pattern, name) is None:
        return "name does not match process_pattern (already named)"
    return None


def _selected(path: Path, cfg: Config) -> bool:
    return _select_reason(path, cfg) is None


def _walk(cfg: Config):
    """All regular files under the inbox, sorted for deterministic ordering."""
    for path in sorted(cfg.paths.inbox_dir.rglob("*")):
        if path.is_file():
            yield path


def iter_inbox(cfg: Config):
    for path in _walk(cfg):
        if _selected(path, cfg):
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


def plan(
    cfg: Config, client: AIClient, ledger: Ledger, logger: logging.Logger | None = None
) -> tuple[list[Proposal], PlanStats]:
    log = logger or _NULL_LOG
    stats = PlanStats()
    proposals: list[Proposal] = []
    existing_subdirs = list_subdirs(cfg.paths.library_dir)
    system_prompt = prompt_mod.build_system_prompt(cfg, existing_subdirs)
    taken = _TakenIndex(cfg.paths.library_dir, cfg.paths.unsorted_subdir)

    log.info(
        "run start: inbox=%s library=%s send_mode=%s model=%s",
        cfg.paths.inbox_dir, cfg.paths.library_dir, cfg.extraction.send_mode, cfg.ai.model,
    )

    for path in _walk(cfg):
        name = path.name

        reason = _select_reason(path, cfg)
        if reason is not None:
            stats.skipped_selection += 1
            log.info("SKIP (criteria): %s - %s", name, reason)
            continue

        file_hash = hash_file(path)
        if ledger.seen(file_hash):
            stats.skipped_seen += 1
            log.info("SKIP (already judged): %s", name)
            continue

        result = extract(path, cfg.extraction)
        if result.error:
            stats.errors += 1
            log.error("FAIL (extract): %s - %s", name, result.error)
            ledger.upsert(LedgerEntry(file_hash=file_hash, original_name=name,
                                      status=STATUS_ERROR, error=result.error))
            continue
        if not result.has_content:
            # Nothing to send in this mode (e.g. an image under send_mode: text, or a
            # PDF with no text layer). Counted as a criteria skip, not an error, and
            # not recorded so it is reconsidered if vision is later enabled.
            stats.skipped_no_content += 1
            log.info("SKIP (criteria): %s - nothing to send in %s mode",
                     name, cfg.extraction.send_mode)
            continue

        stats.sent += 1
        log.info("SEND: %s (%s, %d image(s), %d text chars)",
                 name, result.kind, len(result.images), len(result.text))
        try:
            user_content = prompt_mod.build_user_content(name, result)
            decision = client.decide(
                system_prompt, user_content, existing_subdirs, cfg.naming.allow_new_subdirs
            )
        except AIError as exc:
            stats.errors += 1
            log.error("FAIL (ai): %s - %s", name, exc.detail())
            ledger.upsert(LedgerEntry(file_hash=file_hash, original_name=name,
                                      status=STATUS_ERROR, error=f"ai: {exc}"))
            continue
        except Exception as exc:  # noqa: BLE001 — any other client/parse failure
            stats.errors += 1
            err = f"ai: {type(exc).__name__}: {exc}"
            log.error("FAIL (ai): %s - %s", name, err)
            ledger.upsert(LedgerEntry(file_hash=file_hash, original_name=name,
                                      status=STATUS_ERROR, error=err))
            continue

        proposal = _build_proposal(cfg, path, file_hash, decision, taken)
        proposals.append(proposal)
        if proposal.unsorted:
            stats.unsorted += 1
        else:
            stats.proposed += 1
        log.info("OK: %s -> %s/%s (conf=%.2f%s)", name, proposal.subdir,
                 proposal.new_filename, proposal.confidence,
                 ", unsorted" if proposal.unsorted else "")

        from .ledger import STATUS_PROPOSED, STATUS_UNSORTED

        ledger.upsert(LedgerEntry(
            file_hash=file_hash, original_name=name,
            status=STATUS_UNSORTED if proposal.unsorted else STATUS_PROPOSED,
            decision=decision.model_dump(),
        ))

    _log_summary(log, stats)
    return proposals, stats


def _log_summary(log: logging.Logger, stats: PlanStats) -> None:
    log.info("---- run summary ----")
    log.info("sent to LLM:              %d", stats.sent)
    log.info("processed successfully:   %d (of which %d routed to _Unsorted)",
             stats.proposed + stats.unsorted, stats.unsorted)
    log.info("failed:                   %d", stats.errors)
    log.info("skipped (already judged): %d", stats.skipped_seen)
    log.info("skipped (criteria):       %d (selection %d + nothing-to-send %d)",
             stats.skipped_selection + stats.skipped_no_content,
             stats.skipped_selection, stats.skipped_no_content)


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
