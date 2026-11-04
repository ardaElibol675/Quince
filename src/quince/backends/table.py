"""CSV ⇄ TSV in plain Python, so the data stays exactly as it is.

A spreadsheet program would turn ``007`` into ``7`` and long numbers into ``1E+15``; here
every field is copied as text. The input's encoding (UTF-8 or Windows-1254) and, for CSV,
its separator (comma, semicolon, tab, bar) are detected; the output is UTF-8 with commas or
tabs.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from quince.backends import textfiles
from quince.core.jobs import JobContext, JobError
from quince.core.routes import Route

SEPARATORS = {"csv": ",", "tsv": "\t"}


def convert(ctx: JobContext, src: Path, out: Path, route: Route) -> None:
    text, encoding = textfiles.read_text(src)
    source_sep = "\t" if route.source == "tsv" else textfiles.sniff_delimiter(text)
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=source_sep))
    except csv.Error as exc:
        raise JobError(f"Cannot read the table: {exc}") from exc
    with out.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f, delimiter=SEPARATORS[route.target], lineterminator="\n").writerows(rows)
    if note := textfiles.encoding_note(encoding):
        ctx.note(note)
    if route.source == "csv" and source_sep != ",":
        ctx.note(
            f"Read with {'semicolons' if source_sep == ';' else repr(source_sep)} as separator"
        )
