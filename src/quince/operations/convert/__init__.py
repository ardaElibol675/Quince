"""Conversion routes, one module per category (each defines ``ROUTES``).

``make_run`` turns the routes into a target format into that Convert operation's job: every
selected file is converted on its own, into a new file next to it. Files already in the
target format are skipped. A file that fails does not stop the others (``ctx.item``).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping, Sequence
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
    }
)


def backend(name: str) -> Callable[[JobContext, object, object, Route], None] | None:
    module = BACKEND_MODULES.get(name)
    return importlib.import_module(module).convert if module else None


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
                out = ctx.new_output(item.path, extension=fmt.extension)
                convert(ctx, item.path, out, route)
        ctx.progress(1.0)

    return run
