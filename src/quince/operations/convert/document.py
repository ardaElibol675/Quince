"""Document conversion routes.

Placeholder set from Phase 2; Phase 9 completes the table.
"""

from quince.core.routes import Route

ROUTES = (
    Route("docx", "pdf", "libreoffice"),
    Route("odt", "pdf", "libreoffice"),
    Route("md", "docx", "pandoc"),
)
