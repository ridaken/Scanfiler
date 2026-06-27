"""Configuration loading: YAML + ${ENV_VAR} interpolation + pydantic validation.

Secrets are referenced as ${VAR} in config.yaml and read from the environment, so
they are never stored in plaintext in the file (same convention as
actual-ai-categorizer).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _interpolate_env(value: object) -> object:
    """Recursively replace ${VAR} in strings with os.environ values (missing -> "")."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    return value


class PathsConfig(BaseModel):
    inbox_dir: Path
    library_dir: Path
    unsorted_subdir: str = "_Unsorted"


class AIConfig(BaseModel):
    base_url: str = "http://localhost:8080/v1"
    api_key: str = ""
    model: str = "local-vlm"
    temperature: float = 0.2
    request_timeout_s: float = 90.0
    max_retries: int = 3
    constrained_output: bool = True


class ExtractionConfig(BaseModel):
    pdf_max_pages: int = 2
    raster_dpi: int = 150
    send_mode: Literal["vision", "text", "auto"] = "auto"


class SelectionConfig(BaseModel):
    process_pattern: str = r"^(SCAN|PIC|IMG)[\W_]*\d+"
    process_all: bool = False
    ignore_globs: list[str] = Field(default_factory=lambda: ["*.partial", ".*"])
    min_mtime_age_s: float = 30.0


class NamingConfig(BaseModel):
    date_prefix: bool = True
    max_filename_len: int = 120
    allow_new_subdirs: bool = True


class CategorizationConfig(BaseModel):
    confidence_threshold: float = 0.6


class RAGConfig(BaseModel):
    write_sidecar: bool = True


class SchedulerConfig(BaseModel):
    mode: Literal["once", "loop"] = "once"
    polling_minutes: float = 15.0


class ApplyConfig(BaseModel):
    mode: Literal["review", "auto"] = "review"
    # copy = leave the inbox (rclone mirror) pristine; move = remove the original.
    # copy is the safe default for a one-way mirror: the hash ledger prevents
    # reprocessing the re-synced original, so we never need to delete it.
    action: Literal["copy", "move"] = "copy"
    on_collision: Literal["suffix", "skip", "overwrite"] = "suffix"


class LoggingConfig(BaseModel):
    level: Literal["debug", "info", "warn", "error"] = "info"
    audit_file: Path = Path("./logs/audit.jsonl")
    ledger_db: Path = Path("./state/ledger.sqlite")


class PromptConfig(BaseModel):
    # Free-text blurb describing what this collection of documents is, embedded into
    # the system prompt to help the model interpret ambiguous scans.
    context: str = (
        "These are personal scanned documents: receipts, invoices, medical records, "
        "letters, kids' drawings, and similar. Most are named generically (SCAN0001)."
    )


class AutoUpdateConfig(BaseModel):
    enabled: bool = False
    # 'latest-release' (newest vX.Y.Z tag) or a branch name (e.g. main) to track.
    ref: str = "latest-release"
    install_deps: bool = True              # reinstall when pyproject.toml changed
    restart: bool = True                   # re-exec into the new version this run
    verify_signature: bool = True          # require a trusted commit signature (fail-closed)
    allowed_signers_file: str | None = None  # SSH allowed-signers file; else system gpg trust
    repo_dir: str | None = None            # defaults to the repo root above the package


class Config(BaseModel):
    paths: PathsConfig
    ai: AIConfig = Field(default_factory=AIConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    selection: SelectionConfig = Field(default_factory=SelectionConfig)
    naming: NamingConfig = Field(default_factory=NamingConfig)
    categorization: CategorizationConfig = Field(default_factory=CategorizationConfig)
    rag: RAGConfig = Field(default_factory=RAGConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    apply: ApplyConfig = Field(default_factory=ApplyConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    prompt: PromptConfig = Field(default_factory=PromptConfig)
    auto_update: AutoUpdateConfig = Field(default_factory=AutoUpdateConfig)

    @field_validator("paths")
    @classmethod
    def _validate_paths(cls, v: PathsConfig) -> PathsConfig:
        if v.inbox_dir == v.library_dir:
            # Allowed (in-place reorg of a non-synced folder) but warned about elsewhere.
            pass
        return v


def load_config(path: str | Path) -> Config:
    """Load and validate a config file, interpolating ${ENV_VAR} references."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    interpolated = _interpolate_env(raw)
    return Config.model_validate(interpolated)
