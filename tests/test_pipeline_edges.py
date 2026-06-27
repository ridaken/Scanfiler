"""Selection and routing edge cases in the pipeline."""

from __future__ import annotations

from pathlib import Path

from scanfiler.ai.schema import Decision
from scanfiler.ledger import STATUS_ERROR, Ledger
from scanfiler.pipeline import iter_inbox, plan


class _FixedClient:
    def __init__(self, decision):
        self.decision = decision

    def decide(self, *a, **k):
        return self.decision


def test_process_all_includes_named_files(config, stub_client, workspace):
    config.selection.process_all = True
    with Ledger(config.logging.ledger_db) as ledger:
        proposals, _ = plan(config, stub_client, ledger)
    names = {Path(p.original_path).name for p in proposals}
    assert "TaxReturn2024.pdf" in names  # normally excluded by process_pattern


def test_blocked_new_subdir_routes_to_unsorted(config, workspace):
    config.naming.allow_new_subdirs = False
    client = _FixedClient(
        Decision(filename="Doc", subdir="BrandNew", is_new_subdir=True, confidence=0.99)
    )
    with Ledger(config.logging.ledger_db) as ledger:
        proposals, stats = plan(config, client, ledger)
    assert all(p.subdir == config.paths.unsorted_subdir for p in proposals)
    assert stats.unsorted == len(proposals)


def test_extraction_failure_marks_error(config, stub_client, workspace):
    # An empty PDF extension with no content -> extraction yields no content -> error status.
    bad = workspace["inbox"] / "SCAN0003.pdf"
    bad.write_bytes(b"%PDF-1.4 garbage")
    with Ledger(config.logging.ledger_db) as ledger:
        plan(config, stub_client, ledger)
        entries = ledger.by_status(STATUS_ERROR)
    assert any(e.original_name == "SCAN0003.pdf" for e in entries)


def test_ai_exception_marks_error(config, workspace):
    class _Boom:
        def decide(self, *a, **k):
            raise RuntimeError("model down")

    with Ledger(config.logging.ledger_db) as ledger:
        proposals, stats = plan(config, _Boom(), ledger)
        errors = ledger.by_status(STATUS_ERROR)
    assert proposals == []
    assert stats.errors >= 1
    assert any("ai:" in (e.error or "") for e in errors)


def test_text_mode_skips_image_files_without_sending(config, workspace):
    # send_mode: text must never ship an image; image-only files are skipped, not errored.
    config.extraction.send_mode = "text"
    calls: list[list[dict]] = []

    class _Recording:
        def decide(self, system_prompt, content, subdirs, allow_new):
            calls.append(content)
            return Decision(filename="Doc", subdir="Misc", confidence=0.9)

    with Ledger(config.logging.ledger_db) as ledger:
        proposals, stats = plan(config, _Recording(), ledger)

    assert stats.skipped_no_content >= 1            # the PIC0001.jpg
    assert stats.errors == 0                         # skipping is not an error
    assert not any("PIC0001" in p.original_path for p in proposals)
    # the text docs were processed, but no request ever carried an image part
    sent_parts = [part for c in calls for part in c]
    assert sent_parts and all(p.get("type") != "image_url" for p in sent_parts)


def test_mtime_guard_excludes_fresh_files(config, workspace):
    config.selection.min_mtime_age_s = 3600  # everything is "too fresh"
    assert list(iter_inbox(config)) == []


def test_ignore_globs_exclude_hidden_and_partial(config, workspace):
    (workspace["inbox"] / ".hidden.pdf").write_bytes(b"%PDF-1.4")
    (workspace["inbox"] / "SCAN0007.pdf.partial").write_bytes(b"x")
    selected = {p.name for p in iter_inbox(config)}
    assert ".hidden.pdf" not in selected
    assert "SCAN0007.pdf.partial" not in selected
