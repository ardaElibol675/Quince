"""Subtitle conversion routes (backend ``subtitles``, see ``quince.backends.subtitles``)."""

from quince.core.routes import Route

ROUTES = (
    Route("srt", "vtt", "subtitles"),
    Route("vtt", "srt", "subtitles"),
    Route("srt", "txt", "subtitles"),
    Route("vtt", "txt", "subtitles"),
)
