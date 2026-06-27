"""Per-run logging: a fresh timestamped log file for each plan/run invocation.

The log records, per file, whether it was sent to the LLM, skipped (and why),
processed, or failed (with full diagnostic detail on failure), plus an end-of-run
summary of the counts. Enabled by default; controlled by the [logging] config block.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "error": logging.ERROR,
}


def _null_logger() -> logging.Logger:
    log = logging.getLogger("scanfiler.run.null")
    log.handlers.clear()
    log.addHandler(logging.NullHandler())
    log.propagate = False
    return log


@contextmanager
def open_run_log(cfg) -> Iterator[tuple[logging.Logger, Path | None]]:
    """Yield (logger, path). When logging is disabled, logger is a no-op and path None.

    The file handler is always flushed and closed on exit so the file is complete and
    not left locked (important on Windows).
    """
    log_cfg = cfg.logging
    if not log_cfg.enabled:
        yield _null_logger(), None
        return

    log_dir = Path(log_cfg.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    path = log_dir / f"scanfiler-{ts}.log"

    logger = logging.getLogger(f"scanfiler.run.{ts}")
    logger.handlers.clear()
    logger.setLevel(_LEVELS.get(log_cfg.level, logging.INFO))
    logger.propagate = False
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s"))
    logger.addHandler(handler)
    try:
        yield logger, path
    finally:
        handler.flush()
        handler.close()
        logger.removeHandler(handler)
