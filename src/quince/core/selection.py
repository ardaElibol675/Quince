"""The ``Selection`` model: the files and folders an operation is applied to."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from quince.core import filetype
from quince.core.formats import Category, Format


@dataclass(frozen=True)
class Item:
    path: Path
    is_dir: bool = False
    format: Format | None = None

    @property
    def format_id(self) -> str | None:
        return self.format.id if self.format else None

    @property
    def category(self) -> Category | None:
        return self.format.category if self.format else None


@dataclass(frozen=True)
class Selection:
    """An ordered set of dragged or chosen items.

    A selection is *mixed* when its files do not all share one category; a file of an
    unknown type counts as a category of its own. Mixed selections and selections with
    folders only get file actions (CLAUDE.md Hard Rule 9).
    """

    items: tuple[Item, ...] = ()

    @classmethod
    def from_paths(
        cls,
        paths: Iterable[str | Path],
        detect: Callable[[Path], Format | None] = filetype.detect,
    ) -> Selection:
        items = []
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                items.append(Item(path, is_dir=True))
            else:
                items.append(Item(path, format=detect(path)))
        return cls(tuple(items))

    def __len__(self) -> int:
        return len(self.items)

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def files(self) -> tuple[Item, ...]:
        return tuple(i for i in self.items if not i.is_dir)

    @property
    def folders(self) -> tuple[Item, ...]:
        return tuple(i for i in self.items if i.is_dir)

    @property
    def has_folders(self) -> bool:
        return any(i.is_dir for i in self.items)

    @property
    def format_ids(self) -> frozenset[str | None]:
        """Format ids of the files; None stands for files of unknown type."""
        return frozenset(i.format_id for i in self.files)

    @property
    def is_mixed(self) -> bool:
        return len({i.category for i in self.files}) > 1

    @property
    def category(self) -> Category | None:
        """The category shared by every file, or None (mixed, unknown, folders, empty)."""
        if self.is_empty or self.has_folders or self.is_mixed:
            return None
        return self.files[0].category

    @property
    def is_uniform(self) -> bool:
        """True when Convert and Tools may offer anything: files of one known category."""
        return self.category is not None

    def describe(self) -> str:
        """Short human-readable summary, e.g. ``"3 files, image (JPG, PNG)"``."""
        if self.is_empty:
            return "nothing selected"
        parts = []
        if self.files:
            parts.append(f"{len(self.files)} file" + ("s" if len(self.files) != 1 else ""))
        if self.folders:
            parts.append(f"{len(self.folders)} folder" + ("s" if len(self.folders) != 1 else ""))
        text = ", ".join(parts)
        if self.category is not None:
            labels = sorted({i.format.label for i in self.files if i.format})
            text += f", {self.category.value} ({', '.join(labels)})"
        elif self.is_mixed:
            text += ", mixed types"
        elif self.files and not self.folders:
            text += ", unknown type"
        return text
