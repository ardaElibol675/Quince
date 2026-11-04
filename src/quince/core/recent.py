"""Recently used Copy / Move destinations, in ``~/.local/state/quince/recent-destinations.json``.

This is state, not a setting: it is written whenever a destination is chosen, and a broken
or missing file simply means no recent destinations.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from quince.core import paths

log = logging.getLogger(__name__)

MAX_RECENT = 8
FILE_NAME = "recent-destinations.json"


def default_file() -> Path:
    return paths.state_dir() / FILE_NAME


def _read(path: Path) -> list[Path]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        log.warning("cannot read %s (%s); ignoring it", path, exc)
        return []
    if not isinstance(data, list):
        return []
    return [Path(p) for p in data if isinstance(p, str) and os.path.isabs(p)]


def destinations(path: Path | None = None) -> list[Path]:
    """Recent destination folders that still exist, most recent first."""
    return [p for p in _read(path or default_file()) if p.is_dir()][:MAX_RECENT]


def remember(folder: Path, path: Path | None = None) -> list[Path]:
    """Put ``folder`` first in the list and save it. Returns the new list."""
    path = path or default_file()
    folder = Path(os.path.abspath(folder))
    folders = [folder, *(p for p in _read(path) if p != folder and p.is_dir())][:MAX_RECENT]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".recent-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump([str(p) for p in folders], f, indent=1)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as exc:
        log.warning("cannot save recent destinations to %s: %s", path, exc)
    return folders
