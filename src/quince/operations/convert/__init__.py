"""Conversion routes, one module per category (each defines ``ROUTES``).

``make_run`` turns the routes into a target format into that Convert operation's job: every
selected file is converted on its own, into a new file next to it. Files already in the
target format are skipped. A file that fails does not stop the others (``ctx.item``).

A backend module provides either ``convert(ctx, src, out, route)``, writing one file to
``out``, or ``convert_into(ctx, src, route, outputs)``, which asks ``outputs`` for a file or
a folder itself (page images, one CSV per sheet, Markdown with its images).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

from quince.core import formats
from quince.core.jobs import JobContext, JobError
from quince.core.routes import Route

# Backend name (as in a Route) -> module with ``convert(ctx, src, out, route)``.
# Backends not listed yet are implemented in later phases.
BACKEND_MODULES: Mapping[str, str] = MappingProxyType(
    {
        "imaging": "quince.backends.imaging",
        "ffmpeg": "quince.backends.media",
        "libreoffice": "quince.backends.office",
        "pandoc": "quince.backends.pandoc",
        "table": "quince.backends.table",
        "pdf": "quince.backends.pdf",
        "subtitles": "quince.backends.subtitles",
        "archive": "quince.backends.archive",
        "text": "quince.backends.text",
    }
)


class Outputs:
    """Allocates the result of one file: a file with the target extension, or a folder."""

    def __init__(self, ctx: JobContext, source: Path, extension: str) -> None:
        self._ctx, self._source, self._extension = ctx, source, extension

    def file(self) -> Path:
        return self._ctx.new_output(self._source, extension=self._extension)

    def folder(self) -> Path:
        return self._ctx.new_output(self._source, extension="", directory=True)


def backend(name: str) -> Callable[[JobContext, Path, Route, Outputs], None] | None:
    """The backend's ``convert_into``-style entry point, or None if it is not implemented."""
    module_name = BACKEND_MODULES.get(name)
    if module_name is None:
        return None
    module = importlib.import_module(module_name)
    if hasattr(module, "convert_into"):
        return module.convert_into

    def into(ctx: JobContext, src: Path, route: Route, outputs: Outputs) -> None:
        module.convert(ctx, src, outputs.file(), route)

    return into


def make_run(target: str, routes: Sequence[Route]) -> Callable[[JobContext], None] | None:
    """The job converting a selection to ``target``; None while no route's backend exists."""
    if not any(r.backend in BACKEND_MODULES for r in routes):
        return None
    by_source = {r.source: r for r in routes}
    fmt = formats.get(target)

    def run(ctx: JobContext) -> None:
        todo = [i for i in ctx.selection.files if i.format_id != target]
        skipped = len(ctx.selection.files) - len(todo)
        if skipped:
            ctx.note(f"{skipped} file{'s were' if skipped != 1 else ' was'} already {fmt.label}")
        for index, item in enumerate(todo):
            ctx.check()
            name = item.path.name
            message = f"Converting {name}"
            if len(todo) > 1:
                message = f"Converting {index + 1} of {len(todo)}: {name}"
            span = (index / len(todo), (index + 1) / len(todo))
            with ctx.item(item.path, span):
                ctx.progress(0.0, message)
                route = by_source.get(item.format_id)
                if route is None:
                    source = item.format.label if item.format else "this file"
                    raise JobError(f"Cannot convert {source} to {fmt.label}")
                convert = backend(route.backend)
                if convert is None:
                    raise JobError(f"{route} is not available yet")
                convert(ctx, item.path, route, Outputs(ctx, item.path, fmt.extension))
        ctx.progress(1.0)

    return run
