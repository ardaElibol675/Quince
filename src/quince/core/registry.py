"""The operation registry: every operation, and which ones a selection may use.

Wheels are built only from ``Registry.query`` (CLAUDE.md Hard Rule 1). Operations are
discovered from packages, so adding a route line or an operation module changes the
registry with no other code change.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from types import ModuleType

from quince.core import deps, formats
from quince.core.config import WheelPrefs
from quince.core.deps import Availability
from quince.core.operations import Accepts, Kind, Operation
from quince.core.routes import Route, RouteTable
from quince.core.selection import Selection

OPERATIONS_PACKAGE = "quince.operations"


@dataclass(frozen=True)
class Unavailable:
    """An operation (or a single conversion route) hidden because dependencies are missing."""

    id: str
    label: str
    reason: str


def _needs_conversion(target: str):
    def predicate(selection: Selection) -> bool:
        return any(item.format_id != target for item in selection.files)

    return predicate


def convert_operations(table: RouteTable) -> list[Operation]:
    """One Convert operation per target format.

    A selection is accepted when every file either has a route to the target or already
    is in the target format, and at least one file needs converting.
    """
    ops = []
    for target, routes in table.by_target().items():
        fmt = formats.get(target)
        ops.append(
            Operation(
                id=f"convert.{fmt.category.value}.{fmt.id}",
                kind=Kind.CONVERT,
                label=fmt.label,
                title=fmt.name,
                icon=fmt.category.icon,
                accepts=Accepts(
                    formats=frozenset({target, *(r.source for r in routes)}),
                    predicate=_needs_conversion(target),
                ),
                order=formats.position(target),
                routes=tuple(routes),
            )
        )
    return ops


class Registry:
    def __init__(self, operations: Iterable[Operation] = (), routes: RouteTable | None = None):
        self._ops: dict[str, Operation] = {}
        self.routes = routes or RouteTable()
        for op in operations:
            self.register(op)

    @classmethod
    def from_parts(cls, routes: Iterable[Route], operations: Iterable[Operation] = ()) -> Registry:
        table = RouteTable(routes)
        return cls([*convert_operations(table), *operations], table)

    def register(self, op: Operation) -> None:
        if op.id in self._ops:
            raise ValueError(f"duplicate operation id {op.id!r}")
        unknown = [d for d in op.requirements() if deps.find(d) is None]
        if unknown:
            raise ValueError(f"operation {op.id} requires unknown dependencies {unknown}")
        self._ops[op.id] = op

    def __iter__(self) -> Iterator[Operation]:
        return iter(self._ops.values())

    def __len__(self) -> int:
        return len(self._ops)

    def __contains__(self, op_id: object) -> bool:
        return op_id in self._ops

    def get(self, op_id: str) -> Operation:
        return self._ops[op_id]

    def operations(self, kind: Kind | None = None) -> list[Operation]:
        """Operations of one kind (or all) in their default order."""
        ops = [op for op in self._ops.values() if kind is None or op.kind is kind]
        return sorted(ops, key=lambda op: (op.order, op.label.casefold(), op.id))

    def query(
        self,
        selection: Selection,
        kind: Kind,
        *,
        available: Availability | None = None,
        prefs: WheelPrefs | None = None,
    ) -> list[Operation]:
        """The operations a wheel of ``kind`` shows for ``selection``, in wheel order.

        Convert and Tools offer nothing for mixed selections, folders or unknown file
        types (Hard Rule 9). With ``available``, operations whose dependencies are
        missing for this selection are left out (Hard Rule 7). With ``prefs``, disabled
        operations are left out and the user's order applies.
        """
        if kind is not Kind.ACTION and not selection.is_uniform:
            return []
        ops = [op for op in self.operations(kind) if op.accepts.matches(selection)]
        if available is not None:
            ops = [op for op in ops if not available.missing(op.requirements(selection))]
        if prefs is not None:
            ops = apply_prefs(ops, prefs)
        return ops

    def unavailable(self, available: Availability) -> list[Unavailable]:
        """Everything hidden by missing dependencies, for Settings and ``quince doctor``.

        Convert operations are reported per route, since each route has its own needs.
        """
        hidden = []
        for op in self.operations():
            if op.kind is Kind.CONVERT:
                for route in op.routes:
                    reason = available.reason((*op.requires, *route.all_requires))
                    if reason:
                        hidden.append(Unavailable(op.id, str(route), reason))
            elif reason := available.reason(op.requirements()):
                hidden.append(Unavailable(op.id, op.label, reason))
        return hidden


def apply_prefs(ops: Iterable[Operation], prefs: WheelPrefs) -> list[Operation]:
    """Drop disabled operations; put the ones listed in ``prefs.order`` first, in that order."""
    rank = {op_id: i for i, op_id in enumerate(prefs.order)}
    kept = [op for op in ops if op.id not in prefs.disabled]
    # sorted() is stable, so unlisted operations keep their incoming order.
    return sorted(kept, key=lambda op: rank.get(op.id, len(rank)))


# --- discovery --------------------------------------------------------------


def _modules(package: str) -> Iterator[ModuleType]:
    pkg = importlib.import_module(package)
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if not info.name.startswith("_"):
            yield importlib.import_module(f"{package}.{info.name}")


def discover(package: str = OPERATIONS_PACKAGE) -> Registry:
    """Build the registry from ``<package>.convert`` (``ROUTES`` per module) and
    ``<package>.tools`` / ``<package>.actions`` (``OPERATION`` per module).

    Modules whose name starts with an underscore are skipped.
    """
    routes: list[Route] = []
    for module in _modules(f"{package}.convert"):
        if not hasattr(module, "ROUTES"):
            raise ValueError(f"{module.__name__} must define ROUTES")
        routes.extend(module.ROUTES)

    ops: list[Operation] = []
    for sub, kind in (("tools", Kind.TOOL), ("actions", Kind.ACTION)):
        for module in _modules(f"{package}.{sub}"):
            op = getattr(module, "OPERATION", None)
            if not isinstance(op, Operation):
                raise ValueError(f"{module.__name__} must define OPERATION")
            if op.kind is not kind:
                raise ValueError(f"{module.__name__}: {op.id} is not a {kind.value} operation")
            ops.append(op)
    return Registry.from_parts(routes, ops)
