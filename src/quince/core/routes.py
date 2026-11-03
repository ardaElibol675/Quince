"""Conversion routes as data: ``source format → target format → backend (+ options)``.

Each module in ``quince.operations.convert`` exposes a ``ROUTES`` sequence. Adding a target
for an existing source is one ``Route(...)`` line there; the registry turns routes into
Convert operations, one per target format.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import permutations
from types import MappingProxyType
from typing import Any

from quince.core import formats

# Dependency ids (see ``quince.core.deps``) each backend needs, whatever the route.
BACKEND_REQUIRES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "imaging": ("pillow",),
        "ffmpeg": ("ffmpeg", "ffprobe"),
        "libreoffice": ("soffice",),
        "pandoc": ("pandoc",),
        "pdf": ("pypdf", "pymupdf"),
        "subtitles": ("pysubs2",),
        "archive": (),
    }
)


@dataclass(frozen=True)
class Route:
    source: str
    target: str
    backend: str
    options: Mapping[str, Any] = field(default_factory=dict, hash=False)
    requires: tuple[str, ...] = ()  # in addition to the backend's

    def __post_init__(self) -> None:
        for fmt in (self.source, self.target):
            if formats.find(fmt) is None:
                raise ValueError(f"route {self.source}->{self.target}: unknown format {fmt!r}")
        if self.source == self.target:
            raise ValueError(f"route {self.source}->{self.target}: source equals target")
        if self.backend not in BACKEND_REQUIRES:
            raise ValueError(
                f"route {self.source}->{self.target}: unknown backend {self.backend!r}"
            )
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))

    @property
    def all_requires(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*BACKEND_REQUIRES[self.backend], *self.requires)))

    def __str__(self) -> str:
        return f"{formats.get(self.source).label} → {formats.get(self.target).label}"


def matrix(format_ids: Sequence[str], backend: str, **kwargs: Any) -> tuple[Route, ...]:
    """Routes between every ordered pair of ``format_ids``."""
    return tuple(Route(s, t, backend, **kwargs) for s, t in permutations(format_ids, 2))


class RouteTable:
    """All routes, indexed by (source, target). Each pair may have only one route."""

    def __init__(self, routes: Iterable[Route] = ()) -> None:
        self._routes: dict[tuple[str, str], Route] = {}
        for route in routes:
            self.add(route)

    def add(self, route: Route) -> None:
        key = (route.source, route.target)
        if key in self._routes:
            raise ValueError(f"duplicate route {route}")
        self._routes[key] = route

    def __iter__(self) -> Iterator[Route]:
        return iter(self._routes.values())

    def __len__(self) -> int:
        return len(self._routes)

    def find(self, source: str, target: str) -> Route | None:
        return self._routes.get((source, target))

    def targets(self, source: str) -> list[str]:
        """Target format ids reachable from ``source``, in format registry order."""
        return sorted((t for s, t in self._routes if s == source), key=formats.position)

    def sources(self, target: str) -> list[str]:
        return sorted((s for s, t in self._routes if t == target), key=formats.position)

    def by_target(self) -> dict[str, list[Route]]:
        """Routes grouped by target, both in format registry order."""
        grouped: dict[str, list[Route]] = {}
        for target in sorted({t for _s, t in self._routes}, key=formats.position):
            grouped[target] = [self._routes[(s, target)] for s in self.sources(target)]
        return grouped
