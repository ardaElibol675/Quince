"""The Library: a history of the files Quince made, in ``~/.local/share/quince/history.sqlite3``.

Only outputs of Convert operations and Tools are recorded (Decision Log 2026-11-05): file
actions such as Copy or Move do not make anything new. Files are never touched here; removing
an entry only forgets it. Entries whose file is gone stay in the Library (marked missing) until
the user removes them.

A database that cannot be opened or read never stops Quince: a broken file is moved aside and
a new one started, and a database that cannot be written at all means an empty Library.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from quince.core import paths

T = TypeVar("T")

log = logging.getLogger(__name__)

FILE_NAME = "history.sqlite3"
SCHEMA_VERSION = 1
MAX_ENTRIES = 5000
RECORDED_KINDS = ("convert.", "tool.")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    op_id TEXT NOT NULL,
    title TEXT NOT NULL,
    sources TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS outputs_created ON outputs (created);
"""


def default_file() -> Path:
    return paths.data_dir() / FILE_NAME


def should_record(op_id: str) -> bool:
    """True for operations whose outputs belong in the Library (Convert and Tools)."""
    return op_id.startswith(RECORDED_KINDS)


@dataclass(frozen=True)
class Entry:
    id: int
    path: Path
    op_id: str
    title: str  # what made it, e.g. "Convert to JPG"
    sources: tuple[Path, ...]
    created: float  # Unix time

    @property
    def name(self) -> str:
        return self.path.name or str(self.path)

    @property
    def exists(self) -> bool:
        return os.path.lexists(self.path)

    def matches(self, query: str) -> bool:
        """Every word of ``query`` occurs in the name, the folder, the title or a source name."""
        words = query.casefold().split()
        if not words:
            return True
        text = " ".join(
            [self.name, str(self.path.parent), self.title, *(s.name for s in self.sources)]
        ).casefold()
        return all(w in text for w in words)


class History:
    def __init__(self, path: Path | None = None, *, max_entries: int = MAX_ENTRIES) -> None:
        self.path = path or default_file()
        self.max_entries = max_entries
        self._ready = False

    # --- writing ---------------------------------------------------------------

    def record(
        self,
        outputs: Iterable[Path],
        *,
        op_id: str,
        title: str,
        sources: Sequence[Path] = (),
        when: float | None = None,
    ) -> list[Entry]:
        """Add one entry per output. Returns the new entries (empty when nothing was saved)."""
        when = time.time() if when is None else when
        src = json.dumps([str(p) for p in sources])
        rows = [(str(Path(p)), op_id, title, src, when) for p in outputs]
        if not rows:
            return []

        def write(db: sqlite3.Connection) -> list[Entry]:
            entries = []
            for row in rows:
                cur = db.execute(
                    "INSERT INTO outputs (path, op_id, title, sources, created) "
                    "VALUES (?, ?, ?, ?, ?)",
                    row,
                )
                entries.append(_entry((cur.lastrowid, *row)))
            db.execute(
                "DELETE FROM outputs WHERE id NOT IN "
                "(SELECT id FROM outputs ORDER BY created DESC, id DESC LIMIT ?)",
                (self.max_entries,),
            )
            return entries

        return self._transaction(write, [])

    def remove(self, ids: Iterable[int]) -> None:
        """Forget these entries (the files stay where they are)."""
        rows = [(int(i),) for i in ids]
        if rows:
            self._transaction(lambda db: db.executemany("DELETE FROM outputs WHERE id = ?", rows))

    def clear(self) -> None:
        self._transaction(lambda db: db.execute("DELETE FROM outputs"))

    # --- reading ---------------------------------------------------------------

    def entries(self, query: str = "", *, limit: int | None = None) -> list[Entry]:
        """Entries matching ``query``, newest first."""
        rows = self._transaction(
            lambda db: db.execute(
                "SELECT id, path, op_id, title, sources, created FROM outputs "
                "ORDER BY created DESC, id DESC"
            ).fetchall(),
            [],
        )
        found = [e for e in map(_entry, rows) if e.matches(query)]
        return found if limit is None else found[:limit]

    def recent(self, limit: int) -> list[Entry]:
        """The newest entries whose file still exists, one per path ("Freshly made")."""
        seen: set[Path] = set()
        found: list[Entry] = []
        for entry in self.entries():
            if entry.path in seen or not entry.exists:
                continue
            seen.add(entry.path)
            found.append(entry)
            if len(found) >= limit:
                break
        return found

    # --- internals ---------------------------------------------------------------

    def _transaction(self, work: Callable[[sqlite3.Connection], T], default: T = None) -> T:
        """Run ``work`` in one committed transaction; ``default`` when the database fails."""
        db = self._open()
        if db is None:
            return default
        try:
            with closing(db), db:  # "with db" commits, or rolls back on an error
                return work(db)
        except sqlite3.Error as exc:
            log.warning("Library %s: %s", self.path, exc)
            return default

    def _open(self) -> sqlite3.Connection | None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=5)
        except (OSError, sqlite3.Error) as exc:
            log.warning("cannot open the Library %s: %s", self.path, exc)
            return None
        if self._ready:
            return db
        try:
            self._prepare(db)
            return db
        except sqlite3.DatabaseError as exc:
            db.close()
            return self._start_over(exc)

    def _prepare(self, db: sqlite3.Connection) -> None:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise sqlite3.DatabaseError(f"made by a newer Quince (schema {version})")
        db.executescript(_SCHEMA)
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        db.commit()
        self._ready = True

    def _start_over(self, exc: Exception) -> sqlite3.Connection | None:
        """The file is not a usable database: keep it aside and start a new one."""
        broken = self.path.with_name(self.path.name + f".broken-{int(time.time())}")
        log.warning("the Library %s is unreadable (%s); moved to %s", self.path, exc, broken)
        try:
            os.replace(self.path, broken)
            db = sqlite3.connect(self.path, timeout=5)
            self._prepare(db)
            return db
        except (OSError, sqlite3.Error) as again:
            log.warning("cannot start a new Library %s: %s", self.path, again)
            return None


def _entry(row: Sequence) -> Entry:
    id_, path, op_id, title, sources, created = row
    try:
        src = tuple(Path(s) for s in json.loads(sources) if isinstance(s, str))
    except (TypeError, ValueError):
        src = ()
    return Entry(int(id_), Path(path), str(op_id), str(title), src, float(created))
