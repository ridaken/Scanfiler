"""Image normalization for the VLM: load, downscale, re-encode as PNG."""

from __future__ import annotations

import io
from pathlib import Path

from ..config import ExtractionConfig
from . import ExtractResult

# Longest-edge cap (px). Roughly matches a letter page at the default 150 DPI and
# keeps VLM token/compute cost bounded for large phone photos.
_MAX_EDGE = 1600


def extract_image(path: Path, cfg: ExtractionConfig) -> ExtractResult:
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        longest = max(im.size)
        if longest > _MAX_EDGE:
            scale = _MAX_EDGE / longest
            im = im.resize((round(im.width * scale), round(im.height * scale)))
        buf = io.BytesIO()
        im.save(buf, format="PNG")

    return ExtractResult(kind="image", images=[buf.getvalue()])
