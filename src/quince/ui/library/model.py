"""The Library on the GUI side: records finished jobs and tells the pages when it changed."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from PySide6.QtCore import QObject, Signal

from quince.core.history import Entry, History, should_record
from quince.core.jobs import JobSnapshot, JobState

log = logging.getLogger(__name__)


class LibraryModel(QObject):
    changed = Signal()

    def __init__(self, history: History | None = None) -> None:
        super().__init__()
        self.history = history or History()

    def on_job_event(self, snapshot: JobSnapshot) -> None:
        """Record the outputs of a successful Convert or Tool job."""
        if snapshot.state is not JobState.SUCCEEDED or not snapshot.outputs:
            return
        if not should_record(snapshot.op_id):
            return
        added = self.history.record(
            snapshot.outputs, op_id=snapshot.op_id, title=snapshot.title, sources=snapshot.sources
        )
        if added:
            log.debug("library: recorded %d output(s) of job %d", len(added), snapshot.id)
            self.changed.emit()

    def entries(self, query: str = "", *, limit: int | None = None) -> list[Entry]:
        return self.history.entries(query, limit=limit)

    def recent(self, limit: int) -> list[Entry]:
        return self.history.recent(limit)

    def remove(self, ids: Iterable[int]) -> None:
        self.history.remove(ids)
        self.changed.emit()

    def clear(self) -> None:
        self.history.clear()
        self.changed.emit()
