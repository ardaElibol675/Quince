"""Collision-safe output names (CLAUDE.md Hard Rule 4).

``photo.png`` converted to JPG becomes ``photo.jpg``; if that exists, ``photo (1).jpg``,
``photo (2).jpg``, ... A tool adds a suffix: ``photo-cropped.jpg``. An existing file is
never chosen, so it is never overwritten.
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection
from pathlib import Path

from quince.core.formats import split_extension

# Most Linux file systems limit a single name to 255 bytes.
MAX_NAME_BYTES = 255
MAX_ATTEMPTS = 10_000
_NUMBERED = re.compile(r"^(.*) \((\d+)\)$")


def _fit(stem: str, tail: str) -> str:
    """``stem + tail``, shortening the stem so the name fits ``MAX_NAME_BYTES``."""
    budget = MAX_NAME_BYTES - len(tail.encode())
    if budget <= 0:
        raise ValueError(f"name ending {tail!r} is too long")
    encoded = stem.encode()
    if len(encoded) > budget:
        stem = encoded[:budget].decode(errors="ignore").rstrip()
    return stem + tail


def _taken(path: Path, reserved: Collection[Path]) -> bool:
    # lexists: a dangling symlink still occupies the name.
    return path in reserved or os.path.lexists(path)


def unique_path(path: Path, reserved: Collection[Path] = ()) -> Path:
    """``path`` if it is free, else the first free ``name (n).ext``.

    ``reserved`` holds paths already promised to other outputs of the same batch that
    may not exist on disk yet. A name already ending in ``(n)`` keeps counting from n.
    """
    path = Path(path)
    if not _taken(path, reserved):
        return path
    stem, ext = split_extension(path.name)
    tail = f".{ext}" if ext else ""
    start = 1
    if match := _NUMBERED.match(stem):
        stem, start = match.group(1), int(match.group(2)) + 1
    for n in range(start, start + MAX_ATTEMPTS):
        candidate = path.with_name(_fit(stem, f" ({n}){tail}"))
        if not _taken(candidate, reserved):
            return candidate
    raise FileExistsError(f"no free name for {path}")


def clean_stem(name: str) -> str:
    """A user-typed file name made safe: no folders, no leading/trailing spaces or dots."""
    name = name.replace("/", "-").replace("\0", "")
    return name.strip().strip(".").strip()


def output_path(
    source: Path,
    *,
    extension: str | None = None,
    suffix: str | None = None,
    directory: Path | None = None,
    reserved: Collection[Path] = (),
    stem: str | None = None,
) -> Path:
    """A free path for an output made from ``source``.

    ``extension``: the output's extension without a dot; None keeps the source's, and
    ``""`` means none (an output folder). ``suffix``: added to the stem with a dash, e.g.
    ``"cropped"``. ``directory``: defaults to the source's folder. ``stem``: a name chosen
    by the user instead of the source's (still made free with ``name (1)``).
    """
    source = Path(source)
    source_stem, source_ext = split_extension(source.name)
    ext = source_ext if extension is None else extension.lstrip(".")
    stem = clean_stem(stem or "") or source_stem
    if suffix:
        stem = f"{stem}-{suffix}"
    name = _fit(stem, f".{ext}" if ext else "")
    return unique_path((directory or source.parent) / name, reserved)
