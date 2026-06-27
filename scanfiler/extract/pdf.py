"""PDF extraction via PyMuPDF (fitz): text layer + first-N-page rasterization."""

from __future__ import annotations

from pathlib import Path

from ..config import ExtractionConfig
from . import ExtractResult

# A text layer with at least this many non-whitespace chars across the considered
# pages is treated as "rich enough" to send as text in `auto` mode.
_RICH_TEXT_MIN = 200


def extract_pdf(path: Path, cfg: ExtractionConfig) -> ExtractResult:
    import fitz

    with fitz.open(path) as doc:
        n = min(cfg.pdf_max_pages, doc.page_count)
        text_parts: list[str] = []
        for i in range(n):
            text_parts.append(doc[i].get_text("text"))
        text = "\n".join(text_parts).strip()

        rich = len(text.replace("\n", "").strip()) >= _RICH_TEXT_MIN
        want_images = cfg.send_mode == "vision" or (cfg.send_mode == "auto" and not rich)

        images: list[bytes] = []
        if want_images:
            for i in range(n):
                pix = doc[i].get_pixmap(dpi=cfg.raster_dpi)
                images.append(pix.tobytes("png"))

    # In text mode, never carry images; in auto/vision keep whatever we rendered.
    if cfg.send_mode == "text":
        images = []

    return ExtractResult(kind="pdf", text=text, images=images)
