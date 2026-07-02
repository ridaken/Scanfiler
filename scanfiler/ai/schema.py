"""Constrained-output contract for the model.

Like actual-ai-categorizer constrains `category` to a live enum of real categories,
we constrain `subdir` to the existing subfolder list (the model can only escape it by
explicitly setting is_new_subdir=true). The JSON schema is sent as response_format so
compatible servers (llama-server, etc.) force schema-valid output.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Decision(BaseModel):
    """The model's proposal for a single file."""

    filename: str = Field(description="Base name, NO extension; the tool re-adds the original")
    subdir: str = Field(
        description="Target subfolder; one of the provided list unless is_new_subdir"
    )
    is_new_subdir: bool = False
    doc_type: str = ""
    date: str | None = None  # ISO 'YYYY' / 'YYYY-MM' / 'YYYY-MM-DD'; null if unknown
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    confidence: float = 0.0


def build_response_format(existing_subdirs: list[str], allow_new: bool) -> dict:
    """OpenAI-style response_format with a json_schema constraining the output.

    When new subdirs are allowed we don't hard-enum `subdir` (the model may invent one
    and flag is_new_subdir); we still describe the existing options in the prompt so it
    prefers reuse. When new subdirs are disallowed we enum-constrain `subdir`.
    """
    subdir_schema: dict
    if existing_subdirs and not allow_new:
        subdir_schema = {"type": "string", "enum": existing_subdirs}
    else:
        subdir_schema = {"type": "string"}

    properties = {
        "filename": {"type": "string", "minLength": 1},
        "subdir": subdir_schema,
        "is_new_subdir": {"type": "boolean"},
        # minLength forces non-empty: the model can't satisfy the constraint by
        # emitting "" for doc_type/summary (which it did when they were optional).
        "doc_type": {"type": "string", "minLength": 1},
        "date": {"type": ["string", "null"]},
        "summary": {"type": "string", "minLength": 1},
        "tags": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        # All fields required: forces doc_type/summary to be populated and makes the
        # schema OpenAI strict-mode compliant (portable to cloud providers). `date`
        # stays nullable; `tags` may be an empty array.
        "required": list(properties),
        "properties": properties,
    }
    return {
        "type": "json_schema",
        "json_schema": {"name": "file_decision", "strict": True, "schema": schema},
    }
