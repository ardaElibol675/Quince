"""Spreadsheet conversion routes.

LibreOffice Calc (``quince.backends.office``) for workbooks; CSV ⇄ TSV is done in plain
Python (``quince.backends.table``) so no value is reinterpreted. Markdown and HTML tables
are written by ``quince.backends.text`` (workbooks are read through LibreOffice's CSV).
"""

from quince.core.routes import Route

SHEETS = ("xlsx", "xls", "ods", "csv", "tsv")
TARGETS = ("xlsx", "ods", "csv", "tsv", "pdf")
DELIMITED = {"csv", "tsv"}
LO = ("soffice",)


def _lo(source: str) -> tuple[str, ...]:
    return () if source in DELIMITED else LO


ROUTES = (
    *(
        Route(s, t, "libreoffice")
        for s in SHEETS
        for t in TARGETS
        if s != t and not {s, t} <= DELIMITED
    ),
    Route("csv", "tsv", "table"),
    Route("tsv", "csv", "table"),
    # Tables as Markdown and HTML
    *(Route(s, "md", "text", requires=_lo(s)) for s in SHEETS),
    *(Route(s, "html", "text", requires=(*_lo(s), "pandoc")) for s in SHEETS),
)
