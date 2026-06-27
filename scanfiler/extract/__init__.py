"""Document extraction: dispatch by file type to text and/or page images.

The result feeds the AI call. For PDFs we try the text layer first and rasterize the
first N pages for the vision model; docx is text-only; images go straight to the VLM.
Patterns ported from OCR-Compare (app/core/raster.py uses PyMuPDF/fitz the same way).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..config import ExtractionConfig

PDF_EXTS = {".pdf"}
DOCX_EXTS = {".docx"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic"}

Kind = Literal["pdf", "docx", "image", "unknown"]


@dataclass
class ExtractResult:
    kind: Kind
    text: str = ""
    images: list[bytes] = field(default_factory=list)  # PNG bytes per page
    error: str | None = None

    @property
    def has_content(self) -> bool:
        return bool(self.text.strip()) or bool(self.images)


def classify(path: Path) -> Kind:
    ext = path.suffix.lower()
    if ext in PDF_EXTS:
        return "pdf"
    if ext in DOCX_EXTS:
        return "docx"
    if ext in IMAGE_EXTS:
        return "image"
    return "unknown"


def extract(path: Path, cfg: ExtractionConfig) -> ExtractResult:
    """Extract text and/or page images from a file according to its type."""
    kind = classify(path)
    try:
        if kind == "pdf":
            from . import pdf

            return pdf.extract_pdf(path, cfg)
        if kind == "docx":
            from . import docx as docx_mod

            return docx_mod.extract_docx(path)
        if kind == "image":
            from . import image as image_mod

            return image_mod.extract_image(path, cfg)
        return ExtractResult(kind="unknown", error=f"unsupported extension: {path.suffix}")
    except Exception as exc:  # noqa: BLE001 — surface any extraction failure as a status
        return ExtractResult(kind=kind, error=f"{type(exc).__name__}: {exc}")
