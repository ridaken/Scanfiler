from pathlib import Path

from scanfiler.apply import apply_proposals, undo
from scanfiler.ledger import STATUS_APPLIED, Ledger, hash_file
from scanfiler.pipeline import plan


def _plan(config, stub_client):
    ledger = Ledger(config.logging.ledger_db)
    proposals, stats = plan(config, stub_client, ledger)
    return ledger, proposals, stats


def test_plan_selects_only_unprocessed(config, stub_client):
    ledger, proposals, stats = _plan(config, stub_client)
    names = {Path(p.original_path).name for p in proposals}
    # TaxReturn2024.pdf does not match process_pattern and process_all is False.
    assert "TaxReturn2024.pdf" not in names
    assert {"SCAN0001.pdf", "SCAN0002.docx", "PIC0001.jpg"} == names
    ledger.close()


def test_low_confidence_routes_to_unsorted(config, stub_client):
    ledger, proposals, stats = _plan(config, stub_client)
    img = next(p for p in proposals if p.original_path.endswith("PIC0001.jpg"))
    assert img.unsorted is True
    assert img.subdir == config.paths.unsorted_subdir
    ledger.close()


def test_extension_preserved_and_dated(config, stub_client):
    ledger, proposals, _ = _plan(config, stub_client)
    inv = next(p for p in proposals if p.original_path.endswith("SCAN0001.pdf"))
    assert inv.new_filename.endswith(".pdf")
    assert inv.new_filename.startswith("2025-06-")
    ledger.close()


def test_apply_creates_files_and_sidecars(config, stub_client):
    ledger, proposals, _ = _plan(config, stub_client)
    result = apply_proposals(config, proposals, ledger)
    assert result.applied == len(proposals)
    inv = next(p for p in proposals if p.original_path.endswith("SCAN0001.pdf"))
    dest = config.paths.library_dir / inv.subdir / inv.new_filename
    assert dest.is_file()
    assert dest.with_suffix(dest.suffix + ".json").is_file()
    # copy is the default action: the original stays in the inbox.
    assert Path(inv.original_path).is_file()
    ledger.close()


def test_hash_dedupe_skips_second_pass(config, stub_client):
    ledger, proposals, _ = _plan(config, stub_client)
    apply_proposals(config, proposals, ledger)
    # Re-run plan: every file is now seen by content hash -> nothing proposed.
    proposals2, stats2 = plan(config, stub_client, ledger)
    assert proposals2 == []
    assert stats2.skipped_seen >= 3
    ledger.close()


def test_undo_restores(config, stub_client):
    ledger, proposals, _ = _plan(config, stub_client)
    result = apply_proposals(config, proposals, ledger)
    n = undo(config, last=True)
    assert n == result.applied
    # copy-undo removes the library copies.
    inv = next(p for p in proposals if p.original_path.endswith("SCAN0001.pdf"))
    dest = config.paths.library_dir / inv.subdir / inv.new_filename
    assert not dest.exists()
    ledger.close()


def test_collision_resolution_same_batch(config, stub_client, workspace):
    # Two files that both extract as "invoice" -> same desired name -> must dedupe.
    from tests.conftest import _make_pdf

    _make_pdf(workspace["inbox"] / "SCAN0009.pdf", "INVOICE second one 2025-06")
    ledger, proposals, _ = _plan(config, stub_client)
    invoice_names = [p.new_filename for p in proposals if p.subdir == "Invoices"]
    assert len(invoice_names) == len(set(invoice_names))  # all unique
    ledger.close()
