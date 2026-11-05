"""Document conversion routes.

LibreOffice (``quince.backends.office``) writes the office formats, PDF, plain text and page
images; pandoc (``quince.backends.pandoc``) reads and writes Markdown, HTML and EPUB.
Plain text becomes Markdown in Quince itself (``quince.backends.text``: line breaks kept,
nothing read as markup); its HTML and EPUB are made from that Markdown.
DOC and RTF reach pandoc through LibreOffice, and pandoc's PDF goes through LibreOffice, so
those routes need both.
"""

from quince.core.routes import Route

OFFICE = ("docx", "doc", "odt", "rtf")  # what LibreOffice reads and pandoc may not
EDITABLE = ("docx", "odt")
PANDOC_OUT = ("md", "html", "epub")
LO = ("soffice",)

ROUTES = (
    # Office documents and plain text → office formats, PDF, text, page images
    *(Route(s, t, "libreoffice") for s in (*OFFICE, "txt") for t in (*EDITABLE, "pdf") if s != t),
    *(Route(s, "txt", "libreoffice") for s in OFFICE),
    *(Route(s, t, "libreoffice", requires=("pymupdf",)) for s in OFFICE for t in ("png", "jpg")),
    Route("html", "pdf", "libreoffice"),
    # Markdown, HTML and EPUB out of office documents (the priority routes: → MD)
    *(Route(s, t, "pandoc") for s in EDITABLE for t in PANDOC_OUT),
    *(Route(s, t, "pandoc", requires=LO) for s in ("doc", "rtf") for t in PANDOC_OUT),
    # Markdown, HTML and EPUB in
    *(Route("md", t, "pandoc") for t in ("docx", "odt", "html", "epub", "txt")),
    *(Route("html", t, "pandoc") for t in ("md", "docx", "odt", "epub", "txt")),
    *(Route("epub", t, "pandoc") for t in ("md", "docx", "odt", "html", "txt")),
    *(Route(s, "pdf", "pandoc", requires=LO) for s in ("md", "epub")),
    # Plain text → Markdown (no dependency), HTML and EPUB
    Route("txt", "md", "text"),
    *(Route("txt", t, "text", requires=("pandoc",)) for t in ("html", "epub")),
)
