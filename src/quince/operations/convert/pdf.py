"""PDF conversion routes (backend ``pdf``, see ``quince.backends.pdf``).

Markdown is the priority route. DOCX, ODT, EPUB and HTML are made from that Markdown (flowing,
editable text); PPTX holds every page as a slide picture; XLSX every ruled table as a sheet.
"""

from quince.core.routes import Route

MD = ("pymupdf4llm",)

ROUTES = (
    Route("pdf", "md", "pdf", requires=MD),
    Route("pdf", "txt", "pdf"),
    Route("pdf", "png", "pdf"),
    Route("pdf", "jpg", "pdf"),
    *(Route("pdf", t, "pdf", requires=(*MD, "pandoc")) for t in ("docx", "odt", "epub", "html")),
    Route("pdf", "pptx", "pdf", requires=("soffice",)),
    Route("pdf", "xlsx", "pdf", requires=("soffice",)),
)
