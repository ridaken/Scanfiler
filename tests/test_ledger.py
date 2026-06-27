from scanfiler.ledger import (
    STATUS_APPLIED,
    STATUS_ERROR,
    STATUS_PENDING,
    LedgerEntry,
    hash_file,
    open_ledger,
)


def test_hash_file_stable(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello world")
    assert hash_file(f) == hash_file(f)
    g = tmp_path / "b.bin"
    g.write_bytes(b"hello world!")
    assert hash_file(f) != hash_file(g)


def test_upsert_and_get(tmp_path):
    with open_ledger(tmp_path / "l.sqlite") as ledger:
        ledger.upsert(LedgerEntry(file_hash="h", original_name="x.pdf",
                                  status=STATUS_PENDING))
        e = ledger.get("h")
        assert e is not None and e.status == STATUS_PENDING
        assert e.created_at > 0 and e.updated_at >= e.created_at


def test_upsert_updates_in_place_keeping_created(tmp_path):
    with open_ledger(tmp_path / "l.sqlite") as ledger:
        ledger.upsert(LedgerEntry(file_hash="h", original_name="x", status=STATUS_PENDING))
        created = ledger.get("h").created_at
        ledger.upsert(LedgerEntry(file_hash="h", original_name="x", status=STATUS_APPLIED,
                                  metadata={"k": "v"}))
        e = ledger.get("h")
        assert e.status == STATUS_APPLIED
        assert e.metadata == {"k": "v"}
        assert e.created_at == created


def test_seen_only_for_decided_states(tmp_path):
    with open_ledger(tmp_path / "l.sqlite") as ledger:
        ledger.upsert(LedgerEntry(file_hash="p", original_name="x", status=STATUS_PENDING))
        ledger.upsert(LedgerEntry(file_hash="e", original_name="y", status=STATUS_ERROR))
        ledger.upsert(LedgerEntry(file_hash="a", original_name="z", status=STATUS_APPLIED))
        assert ledger.seen("p") is False   # pending -> retry
        assert ledger.seen("e") is False   # error -> retry
        assert ledger.seen("a") is True
        assert ledger.seen("missing") is False


def test_counts(tmp_path):
    with open_ledger(tmp_path / "l.sqlite") as ledger:
        ledger.upsert(LedgerEntry(file_hash="a", original_name="x", status=STATUS_APPLIED))
        ledger.upsert(LedgerEntry(file_hash="b", original_name="y", status=STATUS_APPLIED))
        ledger.upsert(LedgerEntry(file_hash="c", original_name="z", status=STATUS_ERROR))
        assert ledger.counts() == {STATUS_APPLIED: 2, STATUS_ERROR: 1}
