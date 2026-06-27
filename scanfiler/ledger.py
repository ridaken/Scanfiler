"""SQLite ledger keyed by content hash.

Because the tool *renames* files, a filename-based "processed" list breaks on the
next loop. Keying on sha256 of file contents means a re-pulled SCAN0001.pdf (one-way
rclone re-download) is recognised as already processed and skipped, and identical
re-scans are deduped for free.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

# Lifecycle of a file in the ledger.
STATUS_PENDING = "pending"      # seen, not yet decided
STATUS_PROPOSED = "proposed"    # AI decided, awaiting apply
STATUS_APPLIED = "applied"      # moved into the library
STATUS_UNSORTED = "unsorted"    # low confidence / routed to _Unsorted
STATUS_ERROR = "error"          # extraction or AI failure

_BUF = 1024 * 1024


def hash_file(path: str | Path) -> str:
    """sha256 of file contents, streamed."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_BUF):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class LedgerEntry:
    file_hash: str
    original_name: str
    status: str
    decision: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    new_path: Optional[str] = None
    error: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0


_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    file_hash     TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    status        TEXT NOT NULL,
    decision      TEXT NOT NULL DEFAULT '{}',
    metadata      TEXT NOT NULL DEFAULT '{}',
    new_path      TEXT,
    error         TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
"""


class Ledger:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get(self, file_hash: str) -> Optional[LedgerEntry]:
        row = self._conn.execute(
            "SELECT * FROM files WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return _row_to_entry(row) if row else None

    def seen(self, file_hash: str) -> bool:
        """True if this content has already reached a terminal/decided state."""
        entry = self.get(file_hash)
        return entry is not None and entry.status in (
            STATUS_APPLIED,
            STATUS_PROPOSED,
            STATUS_UNSORTED,
        )

    def upsert(self, entry: LedgerEntry) -> None:
        now = time.time()
        existing = self.get(entry.file_hash)
        created = existing.created_at if existing else now
        self._conn.execute(
            """
            INSERT INTO files
                (file_hash, original_name, status, decision, metadata, new_path,
                 error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_hash) DO UPDATE SET
                original_name = excluded.original_name,
                status        = excluded.status,
                decision      = excluded.decision,
                metadata      = excluded.metadata,
                new_path      = excluded.new_path,
                error         = excluded.error,
                updated_at    = excluded.updated_at
            """,
            (
                entry.file_hash,
                entry.original_name,
                entry.status,
                json.dumps(entry.decision),
                json.dumps(entry.metadata),
                entry.new_path,
                entry.error,
                created,
                now,
            ),
        )
        self._conn.commit()

    def by_status(self, status: str) -> list[LedgerEntry]:
        rows = self._conn.execute(
            "SELECT * FROM files WHERE status = ? ORDER BY updated_at", (status,)
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM files GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}


def _row_to_entry(row: sqlite3.Row) -> LedgerEntry:
    return LedgerEntry(
        file_hash=row["file_hash"],
        original_name=row["original_name"],
        status=row["status"],
        decision=json.loads(row["decision"] or "{}"),
        metadata=json.loads(row["metadata"] or "{}"),
        new_path=row["new_path"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@contextmanager
def open_ledger(db_path: str | Path) -> Iterator[Ledger]:
    ledger = Ledger(db_path)
    try:
        yield ledger
    finally:
        ledger.close()
