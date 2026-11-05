"""Presentation conversion routes (LibreOffice Impress, see ``quince.backends.office``).

The slide text (titles, points, tables, speaker notes) as Markdown or plain text is read by
``quince.backends.text`` from PPTX and ODP directly; PPT goes through PPTX.
"""

from quince.core.routes import Route

SLIDES = ("pptx", "ppt", "odp")

ROUTES = (
    *(Route(s, "pdf", "libreoffice") for s in SLIDES),
    *(Route(s, t, "libreoffice", requires=("pymupdf",)) for s in SLIDES for t in ("png", "jpg")),
    Route("pptx", "odp", "libreoffice"),
    Route("odp", "pptx", "libreoffice"),
    Route("ppt", "pptx", "libreoffice"),
    Route("ppt", "odp", "libreoffice"),
    *(
        Route(s, t, "text", requires=("soffice",) if s == "ppt" else ())
        for s in SLIDES
        for t in ("md", "txt")
    ),
)
