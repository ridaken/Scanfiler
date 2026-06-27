"""Shared fixtures: a temp inbox/library, generated sample files, and a stub AI client."""

from __future__ import annotations

from pathlib import Path

import pytest

from scanfiler.ai.schema import Decision
from scanfiler.config import Config


def _make_pdf(path: Path, text: str) -> None:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def _make_docx(path: Path, text: str) -> None:
    import docx

    d = docx.Document()
    d.add_paragraph(text)
    d.save(str(path))


def _make_image(path: Path) -> None:
    from PIL import Image

    Image.new("RGB", (64, 64), (200, 180, 120)).save(str(path))


@pytest.fixture
def workspace(tmp_path: Path) -> dict:
    inbox = tmp_path / "inbox"
    library = tmp_path / "library"
    inbox.mkdir()
    library.mkdir()

    _make_pdf(
        inbox / "SCAN0001.pdf",
        "INVOICE\nAcme Plumbing\nTotal due: $240.00\nDate: 2025-06-15",
    )
    _make_docx(inbox / "SCAN0002.docx", "Medical record: annual checkup notes for patient.")
    _make_image(inbox / "PIC0001.jpg")
    # Already-named file that should be skipped unless process_all is on.
    _make_pdf(inbox / "TaxReturn2024.pdf", "tax return")

    return {"root": tmp_path, "inbox": inbox, "library": library}


@pytest.fixture
def config(workspace) -> Config:
    root = workspace["root"]
    return Config.model_validate(
        {
            "paths": {
                "inbox_dir": str(workspace["inbox"]),
                "library_dir": str(workspace["library"]),
            },
            "selection": {"min_mtime_age_s": 0},  # files are brand new in tests
            "logging": {
                "audit_file": str(root / "logs" / "audit.jsonl"),
                "ledger_db": str(root / "state" / "ledger.sqlite"),
            },
        }
    )


class StubClient:
    """Deterministic AI stand-in keyed on extracted text/type, like the real client's shape."""

    def __init__(self):
        self.calls = 0

    def decide(self, system_prompt, user_content, existing_subdirs, allow_new) -> Decision:
        self.calls += 1
        blob = " ".join(
            part.get("text", "") for part in user_content if part.get("type") == "text"
        ).lower()
        if "invoice" in blob:
            return Decision(filename="AcmePlumbingInvoice", subdir="Invoices",
                            is_new_subdir=True, doc_type="invoice", date="2025-06",
                            summary="Acme Plumbing invoice.", tags=["plumbing"], confidence=0.9)
        if "medical" in blob:
            return Decision(filename="AnnualCheckupNotes", subdir="Medical",
                            is_new_subdir=True, doc_type="medical_record", date="2025",
                            summary="Annual checkup.", tags=["health"], confidence=0.8)
        # The image carries no text -> low confidence on purpose.
        return Decision(filename="UnknownDrawing", subdir="Drawings", is_new_subdir=True,
                        doc_type="drawing", confidence=0.2)


@pytest.fixture
def stub_client() -> StubClient:
    return StubClient()


@pytest.fixture
def config_file(workspace, config) -> Path:
    """Write the test config to a YAML file on disk for CLI-level tests."""
    import yaml

    path = workspace["root"] / "config.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    return path
