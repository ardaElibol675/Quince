"""Video conversion routes (backend ``ffmpeg``, see ``quince.backends.media``).

Every video format converts to every other one, to an animated GIF and to the audio formats
in ``SOUND`` (the sound track only); an animated GIF converts to the formats in ``FROM_GIF``.
"""

from itertools import permutations

from quince.core.routes import Route

VIDEO = ("mp4", "mov", "mkv", "webm", "avi", "wmv")
SOUND = ("mp3", "m4a", "wav")  # user decision 2026-11-04: MP3 + M4A + WAV from a video
FROM_GIF = ("mp4", "mov", "mkv", "webm")

ROUTES = (
    *(Route(s, t, "ffmpeg") for s, t in permutations(VIDEO, 2)),
    *(Route(s, "gif", "ffmpeg") for s in VIDEO),
    *(Route(s, t, "ffmpeg") for s in VIDEO for t in SOUND),
    *(Route("gif", t, "ffmpeg") for t in FROM_GIF),
)
