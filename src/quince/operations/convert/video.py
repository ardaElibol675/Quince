"""Video conversion routes.

Placeholder set from Phase 2; Phase 8 completes the table.
"""

from quince.core.routes import Route

ROUTES = (
    Route("mov", "mp4", "ffmpeg"),
    Route("mp4", "gif", "ffmpeg"),
    Route("mp4", "mp3", "ffmpeg"),
)
