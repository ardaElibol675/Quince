"""Archive conversion routes (backend ``archive``, see ``quince.backends.archive``).

ZIP, TAR and TAR.GZ need only Python; 7Z needs ``7z``; RAR is input only and needs ``unrar``.
"""

from itertools import permutations

from quince.core.routes import Route

ARCHIVES = ("zip", "tar", "targz", "7z")


def _needs(*format_ids: str) -> tuple[str, ...]:
    return ("7z",) if "7z" in format_ids else ()


ROUTES = (
    *(Route(s, t, "archive", requires=_needs(s, t)) for s, t in permutations(ARCHIVES, 2)),
    *(Route("rar", t, "archive", requires=("unrar", *_needs(t))) for t in ARCHIVES),
)
