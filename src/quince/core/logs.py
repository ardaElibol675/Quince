"""Logging setup: the console plus ``~/.local/state/quince/quince.log`` (rotated)."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from quince.core import paths

FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"
FILE_FORMAT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"
MAX_BYTES = 1_000_000
BACKUPS = 3


def log_file() -> Path:
    return paths.state_dir() / "quince.log"


def setup_logging(verbose: bool = False, *, to_file: bool = True) -> Path | None:
    """Log to the console (DEBUG with ``verbose``, else INFO) and to the log file.

    Returns the log file path, or None when it cannot be written (Quince still runs).
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(FORMAT, DATE_FORMAT))
    root.addHandler(console)
    if not to_file:
        return None
    path = log_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
        )
    except OSError as exc:
        logging.getLogger(__name__).warning("cannot write the log file %s: %s", path, exc)
        return None
    handler.setFormatter(logging.Formatter(FILE_FORMAT))
    root.addHandler(handler)
    return path
