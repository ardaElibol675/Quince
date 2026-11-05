"""Read QR codes: decode QR codes and barcodes in images and show them, ready to copy.

Nothing is written; Quince is offline, so links are shown, never opened.
"""

from __future__ import annotations

from quince.backends import qr
from quince.core.jobs import JobError
from quince.core.operations import Accepts, Host, JobRequest, Kind, Operation, Report, ReportSection
from quince.core.selection import Item, Selection
from quince.operations.tools.images_to_pdf import RASTER


def read_all(items: list[Item]) -> list[tuple[Item, list[qr.Symbol] | str]]:
    results: list[tuple[Item, list[qr.Symbol] | str]] = []
    for item in items:
        try:
            results.append((item, qr.read_codes(item.path, item.format_id)))
        except JobError as exc:
            results.append((item, exc.reason))
    return results


def build_report(results) -> Report:
    sections, texts = [], []
    found = 0
    for item, symbols in results:
        if isinstance(symbols, str):
            sections.append(ReportSection(item.path.name, (("Cannot read", symbols),)))
            continue
        found += len(symbols)
        rows = tuple((s.kind, s.text) for s in symbols)
        sections.append(ReportSection(item.path.name, rows, "No QR code or barcode found"))
        texts += [s.text for s in symbols]
    if found:
        note = (
            f"{found} code{'s' if found != 1 else ''} found. Select one and press Ctrl+C to copy."
        )
    else:
        note = "No QR code or barcode was found. A sharper, larger picture of the code may help."
    return Report("QR Codes", tuple(sections), copy_all="\n".join(texts), note=note)


def _start(selection: Selection, host: Host) -> JobRequest | None:
    results = host.compute("Reading codes…", lambda: read_all(list(selection.files)))
    host.show_report(build_report(results))
    return None


OPERATION = Operation(
    id="tool.read_qr",
    kind=Kind.TOOL,
    label="Read QR",
    title="Read QR codes and barcodes",
    icon="edit-find",
    accepts=Accepts(formats=RASTER),
    requires=("zbarimg",),
    order=80,
    start=_start,
)
