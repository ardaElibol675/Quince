"""Image conversion routes (backend ``imaging``, see ``quince.backends.imaging``).

Every raster format converts to every other one and to PDF; SVG is input only. Adding a
raster format is one entry in ``RASTER``; a single extra route is one ``Route(...)`` line.
"""

from itertools import permutations

from quince.core.routes import Route

RASTER = ("jpg", "png", "webp", "heic", "tiff", "avif", "bmp", "gif")


def _needs(*format_ids: str) -> tuple[str, ...]:
    return ("pillow-heif",) if "heic" in format_ids else ()


ROUTES = (
    *(Route(s, t, "imaging", requires=_needs(s, t)) for s, t in permutations(RASTER, 2)),
    *(Route(s, "pdf", "imaging", requires=("pymupdf", *_needs(s))) for s in RASTER),
    *(Route("svg", t, "imaging", requires=("rsvg-convert", *_needs(t))) for t in RASTER),
    Route("svg", "pdf", "imaging", requires=("rsvg-convert",)),
)
