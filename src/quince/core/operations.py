"""The operation contract (CLAUDE.md Section 5).

Everything that can appear on a wheel is an ``Operation``. Tools and actions are declared
by modules in ``quince.operations.tools`` / ``quince.operations.actions`` (one module-level
``OPERATION`` each); Convert operations are generated from the route table.

An operation does its work in one of three ways:

- ``run(ctx)``: a background job on a worker thread (``quince.core.jobs``).
- ``start(selection, host)``: a step on the GUI thread, run once the drag is over. It may ask
  the user through ``host`` (a folder, an application, ...) and do instant work (clipboard,
  launching a program). It returns a ``JobRequest`` to queue, or None when it is done.
- ``editor``: the name of an editor that opens first and produces the job (Phase 13).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

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
class Choice:
    """One entry of a list the user picks from (``Host.choose``).

    ``icon`` is a freedesktop icon name or an absolute path to an image.
    """

    key: str
    label: str
    icon: str = ""


@dataclass(frozen=True)
class JobRequest:
    """A background job a ``start`` step asks for; ``title`` defaults to the operation's."""

    run: Callable[..., None]
    title: str = ""


class Host(Protocol):
    """Desktop services for ``Operation.start``, provided by the UI on the GUI thread."""

    def choose_folder(
        self, title: str, *, accept_label: str, start: Path | None, recent: Sequence[Path]
    ) -> Path | None:
        """Ask for a folder; None when the user cancels."""
        ...

    def choose(
        self,
        title: str,
        choices: Sequence[Choice],
        *,
        more: Sequence[Choice] = (),
        more_label: str = "",
    ) -> str | None:
        """Ask for one of ``choices`` (and ``more``, in a sub-list); the chosen key or None."""
        ...

    def set_clipboard_text(self, text: str) -> None: ...

    def notify(self, text: str) -> None:
        """A short message for the user (a toast)."""
        ...

    def show_in_files(self, paths: Sequence[Path]) -> None: ...

    def float_items(self, paths: Sequence[Path]) -> None:
        """Put ``paths`` on the Float shelf."""
        ...


@dataclass(frozen=True)
class Operation:
    """A wheel item.

    ``label`` is the short slice text; ``title`` is the full name shown in the wheel center
    (defaults to ``label``). ``requires`` lists dependency ids from ``quince.core.deps``.
    At most one of ``run``, ``start`` and ``editor`` is set (see the module docstring);
    none is set while the operation is a stub. ``routes`` is set only on Convert operations: the
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
    start: Callable[[Selection, Host], JobRequest | None] | None = None
    routes: tuple[Route, ...] = ()
    title: str = ""

    def __post_init__(self) -> None:
        if not self.title:
            object.__setattr__(self, "title", self.label)
        prefix = f"{self.kind.value}."
        if not self.id.startswith(prefix) or len(self.id) == len(prefix):
            raise ValueError(f"operation id {self.id!r} must start with {prefix!r}")
        if sum(x is not None for x in (self.run, self.start, self.editor)) > 1:
            raise ValueError(f"operation {self.id}: set only one of run, start and editor")
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
