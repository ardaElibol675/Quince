"""Create archive: pack the selected files and folders into one ZIP, TAR.GZ or 7Z.

An action (not a tool), so it is offered for folders and mixed selections too
(Decision Log 2026-11-05). A small dialog asks for the format and the name.
"""

from __future__ import annotations

import shutil

from quince.backends import archive
from quince.core import formats
from quince.core.forms import Choice, Form, Text, Values
from quince.core.jobs import JobContext
from quince.core.operations import Accepts, Host, JobRequest, Kind, Operation
from quince.core.selection import Selection

FORMATS = (("zip", "ZIP"), ("targz", "TAR.GZ"), ("7z", "7Z"))


def default_name(selection: Selection) -> str:
    if len(selection) == 1:
        item = selection.items[0]
        return item.path.name if item.is_dir else formats.split_extension(item.path.name)[0]
    return "Archive"


def form_for(selection: Selection, has_7z: bool) -> Form:
    options = tuple(f for f in FORMATS if f[0] != "7z" or has_7z)
    return Form(
        id="archive.create",
        title="Create Archive",
        accept_label="Create",
        fields=(
            Choice("format", "Format", options),
            Text("name", "Name", default=default_name(selection), max_length=200),
        ),
        remember=("format",),
    )


def clean_name(name: str) -> str:
    name = name.replace("/", "-").replace("\x00", "").strip().strip(".")
    return name or "Archive"


def request_for(selection: Selection, values: Values) -> JobRequest:
    target = values["format"]
    extension = formats.get(target).extension
    paths = [item.path for item in selection.items]
    name = clean_name(values["name"])
    stem = formats.split_extension(name)[0] if name.lower().endswith(f".{extension}") else name

    def run(ctx: JobContext) -> None:
        base = paths[0].parent / f"{stem}.{extension}"
        out = ctx.new_output(base, extension=extension)
        archive.pack_items(ctx, paths, target, out)

    return JobRequest(run, f"Create {stem}.{extension}")


def _start(selection: Selection, host: Host) -> JobRequest | None:
    values = host.ask_options(form_for(selection, shutil.which("7z") is not None))
    return None if values is None else request_for(selection, values)


OPERATION = Operation(
    id="action.create_archive",
    kind=Kind.ACTION,
    label="Archive",
    title="Create archive…",
    icon="package-x-generic",
    accepts=Accepts.anything(),
    order=45,
    start=_start,
)
