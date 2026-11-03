"""Audio conversion routes.

Placeholder set from Phase 2; Phase 8 completes the table.
"""

from quince.core.routes import Route

ROUTES = (
    Route("wav", "mp3", "ffmpeg", {"bitrate": "192k"}),
    Route("flac", "mp3", "ffmpeg", {"bitrate": "192k"}),
)
