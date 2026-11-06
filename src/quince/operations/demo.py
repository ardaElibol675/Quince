"""Demo operations for trying the job engine (``quince service --demo-ops``).

Not discovered automatically: this module sits outside the convert/tools/actions packages.
"""

from __future__ import annotations

import time

from quince.core.jobs import JobContext, JobError
from quince.core.operations import Accepts, Kind, Operation

SLOW_SECONDS = 6.0
STEPS = 60


def _write_slowly(ctx: JobContext, fail_at: float | None = None) -> None:
    files = ctx.selection.files
    outputs = [(item, ctx.new_output(item.path, extension="txt", suffix="demo")) for item in files]
    handles = [path.open("w", encoding="utf-8") for _item, path in outputs]
    try:
        for step in range(1, STEPS + 1):
            ctx.check()
            for (item, _path), handle in zip(outputs, handles, strict=True):
                handle.write(f"{item.path.name}: step {step}/{STEPS}\n")
                handle.flush()
            fraction = step / STEPS
            ctx.progress(fraction, f"Step {step} of {STEPS}")
            if fail_at is not None and fraction >= fail_at:
                raise JobError("Demo failure: the job broke halfway on purpose")
            time.sleep(SLOW_SECONDS / STEPS)
    finally:
        for handle in handles:
            handle.close()


def _slow(ctx: JobContext) -> None:
    _write_slowly(ctx)


def _fail(ctx: JobContext) -> None:
    _write_slowly(ctx, fail_at=0.5)


OPERATIONS = (
    Operation(
        id="tool.demo_slow",
        kind=Kind.TOOL,
        label="Slow Demo",
        title="Slow demo job (6 s)",
        icon="media-playback-start",
        accepts=Accepts(),
        order=900,
        suffixes=("demo",),
        run=_slow,
    ),
    Operation(
        id="tool.demo_fail",
        kind=Kind.TOOL,
        label="Fail Demo",
        title="Demo job that fails halfway",
        icon="dialog-error",
        accepts=Accepts(),
        order=901,
        suffixes=("demo",),
        run=_fail,
    ),
)
