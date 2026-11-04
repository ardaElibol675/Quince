"""What the Float shelf holds: references to files and folders, not copies.

Pure logic without Qt, so it is unit tested directly. Items keep the order they were added
in; the same path is never held twice.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path


class ShelfItems:
    def __init__(self) -> None:
        self._items: list[Path] = []

    def __iter__(self) -> Iterator[Path]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, path: object) -> bool:
        return isinstance(path, (str, Path)) and _key(path) in self._items

    @property
    def paths(self) -> list[Path]:
        return list(self._items)

    def add(self, paths: Iterable[str | Path]) -> list[Path]:
        """Add new paths at the end; returns the ones actually added."""
        added = []
        for raw in paths:
            path = _key(raw)
            if path not in self._items:
                self._items.append(path)
                added.append(path)
        return added

    def remove(self, paths: Iterable[str | Path]) -> list[Path]:
        gone = {_key(p) for p in paths}
        removed = [p for p in self._items if p in gone]
        self._items = [p for p in self._items if p not in gone]
        return removed

    def clear(self) -> None:
        self._items.clear()

    def prune(self, exists: Callable[[str], bool] = os.path.lexists) -> list[Path]:
        """Drop items that no longer exist (deleted or moved away); returns them."""
        return self.remove([p for p in self._items if not exists(str(p))])


def _key(path: str | Path) -> Path:
    return Path(os.path.abspath(path))
