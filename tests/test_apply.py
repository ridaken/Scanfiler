"""Edge cases for apply/undo beyond the happy path in test_pipeline."""

from __future__ import annotations

import json

from scanfiler.apply import apply_proposals, undo
from scanfiler.ledger import Ledger
from scanfiler.proposals import Proposal


def _proposal(workspace, **over):
    src = workspace["inbox"] / "SCAN0001.pdf"
    base = dict(file_hash="h1", original_path=str(src), subdir="Receipts",
                new_filename="Receipt.pdf", confidence=0.9)
    base.update(over)
    return Proposal(**base)


def test_move_action_removes_original(config, workspace):
    config.apply.action = "move"
    src = workspace["inbox"] / "SCAN0001.pdf"
    with Ledger(config.logging.ledger_db) as ledger:
        apply_proposals(config, [_proposal(workspace)], ledger)
    assert not src.exists()  # moved out
    assert (config.paths.library_dir / "Receipts" / "Receipt.pdf").is_file()


def test_move_undo_restores_to_inbox(config, workspace):
    config.apply.action = "move"
    src = workspace["inbox"] / "SCAN0001.pdf"
    with Ledger(config.logging.ledger_db) as ledger:
        apply_proposals(config, [_proposal(workspace)], ledger)
    assert not src.exists()
    restored = undo(config, last=True)
    assert restored == 1
    assert src.exists()  # moved back


def test_dry_run_touches_nothing(config, workspace):
    with Ledger(config.logging.ledger_db) as ledger:
        result = apply_proposals(config, [_proposal(workspace)], ledger, dry_run=True)
    assert result.applied == 1
    assert not (config.paths.library_dir / "Receipts").exists()
    assert not config.logging.audit_file.exists()


def test_missing_source_is_skipped(config):
    p = Proposal(file_hash="h", original_path="/does/not/exist.pdf",
                 subdir="X", new_filename="Y.pdf")
    with Ledger(config.logging.ledger_db) as ledger:
        result = apply_proposals(config, [p], ledger)
    assert result.skipped == 1 and result.applied == 0


def test_collision_skip_policy(config, workspace):
    config.apply.on_collision = "skip"
    dest = config.paths.library_dir / "Receipts" / "Receipt.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_text("existing", encoding="utf-8")
    with Ledger(config.logging.ledger_db) as ledger:
        result = apply_proposals(config, [_proposal(workspace)], ledger)
    assert result.skipped == 1
    assert dest.read_text(encoding="utf-8") == "existing"  # untouched


def test_error_is_recorded_not_raised(config, workspace, monkeypatch):
    import scanfiler.apply as apply_mod

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(apply_mod.shutil, "copy2", boom)
    with Ledger(config.logging.ledger_db) as ledger:
        result = apply_proposals(config, [_proposal(workspace)], ledger)
    assert result.errors == 1
    records = [json.loads(x) for x in config.logging.audit_file.read_text().splitlines()]
    assert any(r["action"] == "error" for r in records)


def test_undo_no_audit_file_returns_zero(config):
    assert undo(config, last=True) == 0


def test_sidecar_written_with_metadata(config, workspace):
    p = _proposal(workspace, doc_type="receipt", tags=["x"], summary="hi", date="2025-06")
    with Ledger(config.logging.ledger_db) as ledger:
        apply_proposals(config, [p], ledger)
    sidecar = config.paths.library_dir / "Receipts" / "Receipt.pdf.json"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["doc_type"] == "receipt"
    assert data["source_hash"] == "h1"
