"""Image conversion routes.

Placeholder set from Phase 2; Phase 7 completes the table.
"""

from quince.core.routes import Route

ROUTES = (
    Route("jpg", "png", "imaging"),
    Route("jpg", "webp", "imaging"),
    Route("png", "jpg", "imaging"),
    Route("png", "webp", "imaging"),
    Route("webp", "jpg", "imaging"),
    Route("webp", "png", "imaging"),
    Route("heic", "jpg", "imaging", requires=("pillow-heif",)),
    Route("svg", "png", "imaging", requires=("rsvg-convert",)),
)
