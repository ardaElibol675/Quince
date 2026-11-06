"""Compress: a smaller copy of images, PDFs, audio and video (``backends/compress.py``).

A small dialog asks for the goal each time, prefilled with the last one used for that kind of
file: a quality level, a percentage of the original size, or a size per file.
"""

from __future__ import annotations

from quince.backends import compress
from quince.backends.compress import Goal
from quince.core.formats import Category
from quince.core.forms import Choice, Form, Number, Shown, Values
from quince.core.jobs import JobContext
from quince.core.operations import Accepts, Host, JobRequest, Kind, Operation
from quince.core.selection import Item, Selection
from quince.operations.tools._common import MEDIA_DEPS, each_file, requires_for

UNITS = {"KB": 1024, "MB": 1024 * 1024}
NOTES = {
    Category.IMAGE: "The image keeps its format. A size goal may also make it smaller in pixels.",
    Category.PDF: "Ghostscript rewrites the PDF; text stays selectable and links keep working.",
    Category.AUDIO: "The sound is re-encoded at a lower bit rate in the same format.",
    Category.VIDEO: "A size goal encodes the video twice to hit it closely; this takes a while.",
}


def form_for(category: Category) -> Form:
    return Form(
        id=f"compress.{category.value}",
        title=f"Compress {category.label}",
        accept_label="Compress",
        note=NOTES.get(category, ""),
        fields=(
            Choice(
                "mode",
                "Goal",
                (
                    ("quality", "Quality"),
                    ("percent", "Percentage of the size"),
                    ("size", "File size"),
                ),
            ),
            Choice(
                "level",
                "Quality",
                (
                    ("high", "High (larger file)"),
                    ("medium", "Medium"),
                    ("low", "Low (smallest file)"),
                ),
                default="medium",
            ),
            Number("percent", "Size", 5, 95, 50, suffix="% of the original"),
            Number("size", "At most", 0.1, 100000, 2.0, decimals=1),
            Choice("unit", "Unit", (("MB", "MB"), ("KB", "KB"))),
        ),
        shown_when={
            "level": Shown("mode", ("quality",)),
            "percent": Shown("mode", ("percent",)),
            "size": Shown("mode", ("size",)),
            "unit": Shown("mode", ("size",)),
        },
    )


def goal_from(values: Values) -> Goal:
    size = int(values["size"] * UNITS[values["unit"]])
    return Goal(values["mode"], values["level"], int(values["percent"]), size)


def title_for(goal: Goal, values: Values) -> str:
    if goal.mode == "percent":
        return f"Compress to {goal.percent}%"
    if goal.mode == "size":
        return f"Compress to {values['size']:g} {values['unit']}"
    return f"Compress ({goal.level} quality)"


def _compress_one(goal: Goal):
    def work(ctx: JobContext, item: Item) -> None:
        extension = compress.output_extension(item.format_id)
        out = ctx.new_output(item.path, extension=extension, suffix="compressed")
        compress.compress(ctx, item.path, item.format_id, goal, out)

    return work


def _start(selection: Selection, host: Host) -> JobRequest | None:
    values = host.ask_options(form_for(selection.category))
    if values is None:
        return None
    goal = goal_from(values)
    return JobRequest(lambda ctx: each_file(ctx, _compress_one(goal)), title_for(goal, values))


_MEDIA = compress.AUDIO_COMPRESSIBLE | compress.VIDEO_FORMATS

OPERATION = Operation(
    id="tool.compress",
    kind=Kind.TOOL,
    label="Compress",
    title="Compress…",
    icon="zoom-out",
    accepts=Accepts(formats=frozenset(compress.COMPRESSIBLE)),
    requires_by_format={"pdf": ("gs",), **requires_for(_MEDIA, MEDIA_DEPS)},
    order=10,
    start=_start,
    suffixes=("compressed",),
)
