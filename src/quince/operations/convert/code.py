"""Source code and configuration routes (backend ``text``, see ``quince.backends.text``).

Every code format becomes Markdown (one fenced block tagged with its language, no
dependency), plain text, and HTML and PDF highlighted by pandoc (PDF through DOCX and
LibreOffice). A new code format needs only its line in the format registry.
"""

from quince.core import formats
from quince.core.formats import Category
from quince.core.routes import Route

CODE = tuple(f.id for f in formats.in_category(Category.CODE))
REQUIRES = {"md": (), "txt": (), "html": ("pandoc",), "pdf": ("pandoc", "soffice")}

ROUTES = tuple(Route(s, t, "text", requires=REQUIRES[t]) for s in CODE for t in REQUIRES)
