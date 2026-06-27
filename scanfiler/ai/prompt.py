"""System/user prompt construction for the rename+sort decision."""

from __future__ import annotations

from ..config import Config
from ..extract import ExtractResult

_SYSTEM_TEMPLATE = """\
You are a meticulous archivist. For a single scanned document you propose a concise,
descriptive filename and the best subfolder to file it under.

Collection context: {context}

Rules:
- filename: PascalCase or hyphenated, no extension, no dates unless meaningful to the
  content, <= {max_len} chars. Be specific (vendor, subject, document kind).
- subdir: PREFER one of the existing subfolders below. Only set is_new_subdir=true and
  invent a new one when none genuinely fits.{new_rule}
- date: the document's own date as ISO (YYYY, YYYY-MM, or YYYY-MM-DD); null if none.
- doc_type: a short lowercase noun (receipt, invoice, medical_record, drawing, letter...).
- summary: 1-2 sentences capturing what this document is, for later search.
- tags: 2-6 short lowercase keywords.
- confidence: 0..1, your certainty in the filename+subdir. Be honest; low is fine.

Existing subfolders: {subdirs}
"""

_NO_NEW = "\n- New subfolders are DISABLED: you MUST pick from the existing list."


def build_system_prompt(cfg: Config, existing_subdirs: list[str]) -> str:
    subdirs = ", ".join(existing_subdirs) if existing_subdirs else "(none yet)"
    new_rule = "" if cfg.naming.allow_new_subdirs else _NO_NEW
    return _SYSTEM_TEMPLATE.format(
        context=cfg.prompt.context,
        max_len=cfg.naming.max_filename_len,
        subdirs=subdirs,
        new_rule=new_rule,
    )


def build_user_content(original_name: str, result: ExtractResult) -> list[dict]:
    """Build OpenAI chat 'content' parts: instruction text, extracted text, page images."""
    parts: list[dict] = [
        {
            "type": "text",
            "text": (
                f"Original filename: {original_name}\n"
                f"Detected type: {result.kind}\n"
                "Decide the new filename and subfolder for this document."
            ),
        }
    ]
    if result.text.strip():
        parts.append({"type": "text", "text": f"Extracted text:\n{result.text}"})
    for png in result.images:
        import base64

        b64 = base64.b64encode(png).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            }
        )
    return parts
