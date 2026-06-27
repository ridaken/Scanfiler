"""Filename / subfolder sanitization and collision resolution.

The model returns arbitrary text, so nothing it produces is trusted directly:
cross-platform invalid chars, Windows reserved names, length, path traversal, and
extension hijacking are all defended against here.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

# Chars illegal in filenames on Windows (superset of POSIX restrictions).
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WS = re.compile(r"\s+")
# Windows reserved device names (case-insensitive, with or without extension).
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_component(name: str, max_len: int = 120, fallback: str = "document") -> str:
    """Sanitize a single path component (filename base or subfolder name)."""
    name = unicodedata.normalize("NFC", name)
    name = _INVALID_CHARS.sub("", name)
    name = _WS.sub(" ", name).strip()
    name = name.strip(" .")  # Windows forbids trailing dots/spaces
    if not name:
        return fallback
    if name.upper() in _RESERVED or name.split(".")[0].upper() in _RESERVED:
        name = f"_{name}"
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .") or fallback
    return name


def sanitize_subdir(subdir: str, fallback: str = "_Unsorted") -> str:
    """Sanitize a (possibly multi-segment) subfolder, blocking traversal/absolutes.

    Always returns a relative, forward-slash path that stays under the library root.
    """
    subdir = subdir.strip().replace("\\", "/")
    parts = []
    for seg in PurePosixPath(subdir).parts:
        if seg in ("", ".", "..", "/"):
            continue  # drop traversal and absolute anchors
        parts.append(sanitize_component(seg, fallback=fallback))
    if not parts:
        return fallback
    return "/".join(parts)


def normalize_extension(ext: str) -> str:
    """Lowercase, dot-prefixed extension preserved from the ORIGINAL file."""
    if not ext:
        return ""
    return "." + ext.lstrip(".").lower()


def with_date_prefix(base: str, iso_date: str | None, enabled: bool) -> str:
    """Prefix the base name with a sortable YYYY-MM when enabled and available."""
    if not enabled or not iso_date:
        return base
    m = re.match(r"^(\d{4})(?:-(\d{2}))?", iso_date)
    if not m:
        return base
    prefix = m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")
    if base.startswith(prefix):
        return base
    return f"{prefix}-{base}"


def resolve_collision(
    desired: str, ext: str, taken: set[str], policy: str = "suffix"
) -> str | None:
    """Return a non-colliding 'name.ext', or None if policy says to skip.

    `taken` holds already-claimed lowercase 'name.ext' strings (existing files +
    earlier proposals in this batch). The chosen name is added by the caller.
    """
    candidate = f"{desired}{ext}"
    if candidate.lower() not in taken:
        return candidate
    if policy == "overwrite":
        return candidate
    if policy == "skip":
        return None
    # suffix policy: append -2, -3, ...
    i = 2
    while True:
        candidate = f"{desired}-{i}{ext}"
        if candidate.lower() not in taken:
            return candidate
        i += 1
