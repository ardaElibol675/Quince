"""Presentation conversion routes (LibreOffice Impress, see ``quince.backends.office``)."""

from quince.core.routes import Route

SLIDES = ("pptx", "ppt", "odp")

ROUTES = (
    *(Route(s, "pdf", "libreoffice") for s in SLIDES),
    *(Route(s, t, "libreoffice", requires=("pymupdf",)) for s in SLIDES for t in ("png", "jpg")),
    Route("pptx", "odp", "libreoffice"),
    Route("odp", "pptx", "libreoffice"),
    Route("ppt", "pptx", "libreoffice"),
    Route("ppt", "odp", "libreoffice"),
)
