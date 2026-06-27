"""The review mapping file: one JSON object per line (JSONL).

`plan` writes it; the user reviews/edits it; `apply` reads it back. JSONL is
diff-friendly and trivially hand-editable (fix a name, change a subdir, or delete a
line to skip a file).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Proposal:
    file_hash: str
    original_path: str   # absolute path in the inbox
    subdir: str          # relative to library_dir (already sanitized)
    new_filename: str    # final name including extension (already collision-resolved)
    is_new_subdir: bool = False
    confidence: float = 0.0
    doc_type: str = ""
    date: str | None = None
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    # If True, apply routes to the _Unsorted subdir instead of `subdir`.
    unsorted: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def write_proposals(path: str | Path, proposals: list[Proposal]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for p in proposals:
            f.write(p.to_json() + "\n")


def read_proposals(path: str | Path) -> Iterator[Proposal]:
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            yield Proposal(**data)
