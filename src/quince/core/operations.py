"""The operation contract (CLAUDE.md Section 5).

Everything that can appear on a wheel is an ``Operation``. Tools and actions are declared
by modules in ``quince.operations.tools`` / ``quince.operations.actions`` (one module-level
``OPERATION`` each); Convert operations are generated from the route table.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from quince.core.formats import Category
from quince.core.routes import Route
from quince.core.selection import Selection

DEFAULT_ORDER = 1000


class Kind(str, Enum):
    CONVERT = "convert"
    TOOL = "tool"
    ACTION = "action"

    @property
    def wheel(self) -> str:
        """Name of the wheel showing this kind (also used in config and triggers)."""
        return _WHEELS[self]

    @classmethod
    def from_wheel(cls, wheel: str) -> Kind:
        for kind, name in _WHEELS.items():
            if name == wheel:
                return kind
        raise ValueError(f"unknown wheel {wheel!r}")


_WHEELS = {Kind.CONVERT: "convert", Kind.TOOL: "tools", Kind.ACTION: "actions"}


@dataclass(frozen=True)
class Accepts:
    """Which selections an operation supports.

    With neither ``formats`` nor ``categories`` set, files of any type are accepted.
    ``predicate`` adds a custom condition on the whole selection.
    """

    formats: frozenset[str] = frozenset()
    categories: frozenset[Category] = frozenset()
    min_items: int = 1
    max_items: int | None = None
    folders: bool = False
    mixed: bool = False
    predicate: Callable[[Selection], bool] | None = None

    @classmethod
    def anything(cls) -> Accepts:
        """Any files and folders, in any number (file actions)."""
        return cls(folders=True, mixed=True)

    def _accepts_file_type(self, format_id: str | None, category: Category | None) -> bool:
        if not self.formats and not self.categories:
            return True
        return format_id in self.formats or category in self.categories

    def matches(self, selection: Selection) -> bool:
        count = len(selection)
        if count == 0 or count < self.min_items:
            return False
        if self.max_items is not None and count > self.max_items:
            return False
        if selection.has_folders and not self.folders:
            return False
        if selection.is_mixed and not self.mixed:
            return False
        if not all(self._accepts_file_type(i.format_id, i.category) for i in selection.files):
            return False
        return self.predicate is None or self.predicate(selection)


@dataclass(frozen=True)
class Operation:
    """A wheel item.

    ``label`` is the short slice text; ``title`` is the full name shown in the wheel center
    (defaults to ``label``). ``requires`` lists dependency ids from ``quince.core.deps``.
    ``run`` is a direct job;
    ``editor`` names an editor that opens first and produces the job. Both may be unset
    while the operation is a stub. ``routes`` is set only on Convert operations: the
    routes into the target format, one per source format.
    """

    id: str
    kind: Kind
    label: str
    icon: str
    accepts: Accepts
    requires: tuple[str, ...] = ()
    order: int = DEFAULT_ORDER
    run: Callable[..., Any] | None = None
    editor: str | None = None
    routes: tuple[Route, ...] = ()
    title: str = ""

    def __post_init__(self) -> None:
        if not self.title:
            object.__setattr__(self, "title", self.label)
        prefix = f"{self.kind.value}."
        if not self.id.startswith(prefix) or len(self.id) == len(prefix):
            raise ValueError(f"operation id {self.id!r} must start with {prefix!r}")
        if self.run is not None and self.editor is not None:
            raise ValueError(f"operation {self.id}: set either run or editor, not both")
        if self.routes and self.kind is not Kind.CONVERT:
            raise ValueError(f"operation {self.id}: only convert operations have routes")

    def route_for(self, format_id: str | None) -> Route | None:
        return next((r for r in self.routes if r.source == format_id), None)

    def requirements(self, selection: Selection | None = None) -> tuple[str, ...]:
        """Dependency ids needed to run this operation on ``selection``.

        For Convert operations only the routes the selection uses count, so a missing
        LibreOffice hides DOCX → PNG but not JPG → PNG. Without a selection, every
        route counts.
        """
        needed = list(self.requires)
        if selection is None:
            used = self.routes
        else:
            used = tuple(r for r in map(self.route_for, selection.format_ids) if r)
        for route in used:
            needed += route.all_requires
        return tuple(dict.fromkeys(needed))
