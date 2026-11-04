"""Audio conversion routes (backend ``ffmpeg``, see ``quince.backends.media``).

Every audio format converts to every other one. Adding a format is one entry in ``AUDIO``.
"""

from itertools import permutations

from quince.core.routes import Route

AUDIO = ("mp3", "m4a", "wav", "flac", "ogg", "opus", "aiff", "wma")

ROUTES = tuple(Route(s, t, "ffmpeg") for s, t in permutations(AUDIO, 2))
