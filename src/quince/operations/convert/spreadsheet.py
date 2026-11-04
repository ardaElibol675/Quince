"""Spreadsheet conversion routes.

LibreOffice Calc (``quince.backends.office``) for workbooks; CSV ⇄ TSV is done in plain
Python (``quince.backends.table``) so no value is reinterpreted.
"""

from quince.core.routes import Route

SHEETS = ("xlsx", "xls", "ods", "csv", "tsv")
TARGETS = ("xlsx", "ods", "csv", "tsv", "pdf")
DELIMITED = {"csv", "tsv"}

ROUTES = (
    *(
        Route(s, t, "libreoffice")
        for s in SHEETS
        for t in TARGETS
        if s != t and not {s, t} <= DELIMITED
    ),
    Route("csv", "tsv", "table"),
    Route("tsv", "csv", "table"),
)
