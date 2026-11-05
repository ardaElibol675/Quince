"""Metadata: show what a file carries (camera, GPS position, author, tags, ...) and, on
request, write a copy without it (``backends/metadata.py``). The original is never changed.
"""

from __future__ import annotations

from quince.backends import metadata
from quince.core.jobs import JobContext, JobError
from quince.core.operations import (
    Accepts,
    Choice,
    Host,
    JobRequest,
    Kind,
    Operation,
    Report,
    ReportSection,
)
from quince.core.selection import Item, Selection
from quince.operations.tools._common import MEDIA_DEPS, each_file, requires_for

STRIP = "strip"


def read_all(items: list[Item]) -> list[tuple[Item, metadata.Metadata | str]]:
    results: list[tuple[Item, metadata.Metadata | str]] = []
    for item in items:
        try:
            results.append((item, metadata.read(item.path, item.format_id)))
        except JobError as exc:
            results.append((item, exc.reason))
    return results


def build_report(results) -> Report:
    sections: list[ReportSection] = []
    lines: list[str] = []
    found = 0
    for item, meta in results:
        name = item.path.name
        if isinstance(meta, str):
            sections.append(ReportSection(name, (("Cannot read", meta),)))
            lines.append(f"{name}\nCannot read: {meta}")
            continue
        found += bool(meta.rows)
        sections.append(ReportSection(name, tuple(meta.info)))
        sections.append(ReportSection(f"{name} · Metadata", tuple(meta.rows), "No metadata"))
        lines.append(f"{name}\n{metadata.describe(meta.info)}\n{metadata.describe(meta.rows)}")
    actions = ()
    if found:
        label = "Save Copy Without Metadata" if found == 1 else f"Save {found} Copies Without It"
        actions = (Choice(STRIP, label),)
        note = "Removing writes a copy next to the original (…-no-metadata); the original stays."
    else:
        note = "No metadata was found."
    return Report("Metadata", tuple(sections), actions, "\n\n".join(lines).strip(), note)


def _strip(paths: set):
    def work(ctx: JobContext, item: Item) -> None:
        if item.path not in paths:
            return
        out = ctx.new_output(item.path, suffix="no-metadata")
        metadata.strip(ctx, item.path, item.format_id, out)

    return work


def _start(selection: Selection, host: Host) -> JobRequest | None:
    results = host.compute("Reading metadata…", lambda: read_all(list(selection.files)))
    if host.show_report(build_report(results)) != STRIP:
        return None
    with_metadata = {item.path for item, meta in results if not isinstance(meta, str) and meta.rows}
    return JobRequest(lambda ctx: each_file(ctx, _strip(with_metadata)), "Remove metadata")


OPERATION = Operation(
    id="tool.metadata",
    kind=Kind.TOOL,
    label="Metadata",
    title="Metadata (view / remove)",
    icon="document-properties",
    accepts=Accepts(formats=frozenset(metadata.SUPPORTED)),
    requires_by_format=requires_for(metadata.MEDIA_FORMATS, MEDIA_DEPS),
    order=20,
    start=_start,
)
