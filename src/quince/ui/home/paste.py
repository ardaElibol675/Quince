"""Ctrl+V in Home: files from the clipboard, or pasted images and text saved as files.

- Files copied in a file manager (``text/uri-list`` or GNOME's ``x-special/gnome-copied-files``)
  are used where they are.
- Text that is only paths or ``file://`` links of existing files counts as those files.
- An image is saved as PNG, other text as UTF-8 ``.txt``, in ``~/.local/share/quince/pasted/``
  (Decision Log 2026-11-05); their outputs go next to them like any other source.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QImage

from quince.core import naming, paths
from quince.core.jobs import JobError, move_no_replace

log = logging.getLogger(__name__)

GNOME_COPIED = "x-special/gnome-copied-files"


def pasted_dir() -> Path:
    return paths.data_dir() / "pasted"


def _local(uri_or_path: str) -> Path | None:
    text = uri_or_path.strip()
    if text.startswith("file://"):
        parsed = urlparse(text)
        if parsed.netloc not in ("", "localhost"):
            return None
        text = unquote(parsed.path)
    if not os.path.isabs(text):
        return None
    return Path(text)


def _existing(candidates: list[str]) -> list[Path] | None:
    """The paths, when every non-empty line is an existing local file or folder."""
    lines = [c for c in candidates if c.strip()]
    found = [_local(c) for c in lines]
    if not lines or any(p is None or not os.path.lexists(p) for p in found):
        return None
    return list(dict.fromkeys(found))


def clipboard_files(mime: QMimeData) -> list[Path] | None:
    """Files the clipboard refers to, or None when it holds no file references."""
    if mime.hasFormat(GNOME_COPIED):
        lines = bytes(mime.data(GNOME_COPIED)).decode("utf-8", "replace").splitlines()
        if lines and lines[0] in ("copy", "cut"):
            lines = lines[1:]
        if (found := _existing(lines)) is not None:
            return found
    if mime.hasUrls():
        urls = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        if (found := _existing(urls)) is not None:
            return found
    if mime.hasText():
        return _existing(mime.text().splitlines())
    return None


def _place(folder: Path, extension: str, now: datetime, write) -> Path:
    """Write through a temporary file and move it to a free name (never over a file)."""
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".quince-paste-", suffix=f".{extension}", dir=folder)
    os.close(fd)
    try:
        write(Path(tmp))
        target = folder / f"pasted-{now:%Y-%m-%d-%H%M%S}.{extension}"
        while True:
            target = naming.unique_path(target)
            try:
                move_no_replace(Path(tmp), target)
                return target
            except FileExistsError:
                continue  # taken in the meantime
    finally:
        Path(tmp).unlink(missing_ok=True)


def save_image(image: QImage, folder: Path | None = None, now: datetime | None = None) -> Path:
    def write(tmp: Path) -> None:
        if not image.save(str(tmp), "PNG"):
            raise JobError("Could not save the pasted image")

    return _place(folder or pasted_dir(), "png", now or datetime.now(), write)


def save_text(text: str, folder: Path | None = None, now: datetime | None = None) -> Path:
    return _place(
        folder or pasted_dir(),
        "txt",
        now or datetime.now(),
        lambda tmp: tmp.write_text(text, encoding="utf-8"),
    )


def paste(mime: QMimeData | None, folder: Path | None = None) -> list[Path]:
    """The files to add for this clipboard content (saving images and text first).

    Raises ``JobError`` with a short reason when there is nothing usable or saving failed.
    """
    if mime is None:
        raise JobError("The clipboard is empty")
    files = clipboard_files(mime)
    if files:
        return files
    try:
        if mime.hasImage():
            image = QImage(mime.imageData())
            if not image.isNull():
                path = save_image(image, folder)
                log.info("pasted image saved as %s", path)
                return [path]
        if mime.hasText() and mime.text().strip():
            path = save_text(mime.text(), folder)
            log.info("pasted text saved as %s", path)
            return [path]
    except OSError as exc:
        raise JobError(f"Could not save what was pasted: {exc.strerror or exc}") from exc
    raise JobError("The clipboard holds no files, image or text")
