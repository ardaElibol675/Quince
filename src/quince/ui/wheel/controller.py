"""Connects the global drag watcher to the wheel (the Tangerine-like flow).

One wheel session per drag:

1. ``DragStarted``: a file drag began. The watcher then reports the dragged files
   (``DragFiles``), read from the drag source before any wheel is shown. Detecting their
   types and querying the registry runs on a worker thread.
2. ``TriggerTapped``: open the wheel at the pointer (or switch it in place). If the files are
   not known yet, the wheel shows a pending state and fills in when they are. The pointer is
   nudged by 1 px so the drag source re-targets the wheel at once; otherwise Enter would drop
   on whatever window was under the pointer before the wheel appeared.
3. Raw keys (``DragKey``) drive the wheel; a right or middle click (``DragCancel``) closes it.
4. A drop on a slice or a keyboard choice emits ``operation_chosen``. The wheel stays mapped
   and invisible until the drag is over, so any late drop lands on it and is ignored.
5. ``DragEnded``: after a short grace period the wheel is released (hidden). When the choice
   came from a drop, there is nothing left to swallow, so it is released at once.

Once the wheel closed, further taps in the same drag are ignored. ``after_drag`` runs a
callback once the drag is over and the wheel is released (file actions that open a dialog).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QPoint, QTimer, Signal
from PySide6.QtGui import QCursor

from quince.core import config, deps
from quince.core.operations import Operation
from quince.core.registry import Registry
from quince.core.selection import Selection
from quince.input import drag
from quince.ui.wheel.model import WheelContent, build_contents, pending_content
from quince.ui.wheel.window import WheelWindow

log = logging.getLogger(__name__)

# After the drag ends, wait this long for the drop on the wheel before releasing it.
DROP_GRACE_MS = 400


def nudge_pointer() -> None:
    """Move the pointer 1 px and back so the drag source looks for a new drop target."""
    pos = QCursor.pos()
    QCursor.setPos(pos + QPoint(1, 0))
    QCursor.setPos(pos)


class DragWheelController(QObject):
    operation_chosen = Signal(object, object)  # Operation, Selection
    # Worker thread -> GUI thread: drag time, paths, selection, contents.
    _resolved = Signal(int, object, object, object)

    def __init__(
        self,
        wheel: WheelWindow,
        registry: Registry,
        *,
        load_config: Callable[[], config.Config] = config.load,
        probe: Callable[[], deps.Availability] = deps.Availability.probe,
        submit: Callable[[Callable[[], None]], object] | None = None,
        nudge: Callable[[], None] = nudge_pointer,
        grace_ms: int = DROP_GRACE_MS,
    ) -> None:
        super().__init__()
        self.wheel = wheel
        self.registry = registry
        self._load_config = load_config
        self._probe = probe
        if submit is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quince-sel")
            submit = self._executor.submit
        self._submit = submit
        self._nudge = nudge
        self._grace_ms = grace_ms

        self._session: int | None = None  # time of the current (or last) drag
        self._dragging = False
        self._used = False  # the wheel was shown and closed in this drag
        self._no_files = False
        self._paths: tuple[str, ...] | None = None
        self._selection: Selection | None = None
        self._contents: Mapping[str, WheelContent] | None = None
        self._wheel_name: str | None = None
        self._dropped = False  # the choice came from a drop on the wheel
        self._pending: list[Callable[[], None]] = []  # waiting for the drag to be over

        self._resolved.connect(self._on_resolved)
        wheel.chosen.connect(self._on_chosen)
        wheel.closed.connect(self._on_closed)
        wheel.drag_entered.connect(self._on_drag_entered)

    # --- watcher signals --------------------------------------------------------

    def handle(self, sig: drag.Signal) -> None:
        if isinstance(sig, drag.DragStarted):
            self._on_drag_started(sig.time)
        elif isinstance(sig, drag.DragFiles):
            self._on_drag_files(sig)
        elif isinstance(sig, drag.TriggerTapped):
            self._on_tap(sig.wheel)
        elif isinstance(sig, drag.DragKey):
            if self.wheel.is_open:
                self.wheel.handle_key(sig.key)
        elif isinstance(sig, drag.DragCancel):
            if self.wheel.is_open:
                self.wheel.close_wheel("cancel click")
        elif isinstance(sig, drag.DragEnded):
            self._on_drag_ended()

    def _on_drag_started(self, time: int) -> None:
        if self.wheel.isVisible():
            self.wheel.release()  # leftover from the previous drag's grace period
        self._session = time
        self._dragging = True
        self._used = self._no_files = False
        self._paths = self._selection = self._contents = None
        self._wheel_name = None
        self._dropped = False
        log.info("drag started")

    def _on_drag_files(self, sig: drag.DragFiles) -> None:
        if sig.drag_time != self._session or sig.paths is None:
            return
        if not sig.paths:
            log.info("drag carries no local files; no wheel for this drag")
            self._no_files = True
            if self.wheel.is_open or self.wheel.holding:
                self.wheel.release()
            return
        self._resolve(sig.paths)

    def _on_tap(self, wheel: str) -> None:
        if not self._dragging or self._used or self._no_files:
            log.info("tap %s ignored", wheel)
            return
        content = self._content(wheel)
        self._wheel_name = wheel
        if self.wheel.is_open:
            self.wheel.switch_to(content)
            log.info("wheel switched to %s: %s", wheel, _describe(content))
            return
        self.wheel.open_at(QCursor.pos(), content, hold=True, owner=self)
        center = self.wheel.center
        log.info("wheel %s opened at %d,%d: %s", wheel, center.x(), center.y(), _describe(content))
        self._nudge()

    def _on_drag_ended(self) -> None:
        self._dragging = False
        log.info("drag ended")
        session = self._session
        grace = 0 if self._dropped else self._grace_ms
        QTimer.singleShot(grace, self, lambda: self._release(session))

    def _release(self, session: int | None) -> None:
        if session == self._session and not self._dragging:
            if self.wheel.owner is self:
                self.wheel.release()
            self._run_pending()

    # --- after the drag -------------------------------------------------------------

    @property
    def busy(self) -> bool:
        """A drag is in progress or the wheel is still mapped for it."""
        return self.in_drag_session

    @property
    def in_drag_session(self) -> bool:
        """A drag is in progress, or the wheel is still open or held for the last one."""
        return self._dragging or (
            self.wheel.owner is self and (self.wheel.is_open or self.wheel.holding)
        )

    def after_drag(self, callback: Callable[[], None]) -> None:
        """Run ``callback`` on the GUI thread once the drag is over and the wheel released."""
        self._pending.append(callback)
        if not self.busy:
            QTimer.singleShot(0, self, self._run_pending)

    def _run_pending(self) -> None:
        if self.busy:
            return
        pending, self._pending = self._pending, []
        for callback in pending:
            try:
                callback()
            except Exception:
                log.exception("callback after the drag failed")

    # --- selection ----------------------------------------------------------------

    def _content(self, wheel: str) -> WheelContent:
        if self._contents is None:
            return pending_content(wheel)
        return self._contents[wheel]

    def _resolve(self, paths: tuple[str, ...]) -> None:
        """Detect file types and build all wheel contents on the worker thread."""
        if paths == self._paths:
            return
        self._paths = paths
        session = self._session

        def work() -> None:
            try:
                selection = Selection.from_paths(paths)
                contents = build_contents(
                    selection, self.registry, self._probe(), self._load_config()
                )
            except Exception:
                log.exception("could not read the dragged files")
                return
            self._resolved.emit(session, paths, selection, contents)

        self._submit(work)

    def _on_resolved(self, session: int, paths, selection: Selection, contents) -> None:
        if session != self._session or paths != self._paths:
            return  # a newer drag or newer data
        self._selection, self._contents = selection, contents
        log.info("dragged: %s", selection.describe())
        if self.wheel.is_open and self._wheel_name is not None:
            content = contents[self._wheel_name]
            self.wheel.set_content(content)
            log.info("wheel %s contents: %s", self._wheel_name, _describe(content))

    # --- wheel signals ------------------------------------------------------------

    def _on_drag_entered(self, paths: list) -> None:
        if self.wheel.owner is not self:
            return
        if paths:
            self._resolve(tuple(paths))
        elif self._paths is None and self.wheel.is_open:
            log.info("drag carries no local files; closing the wheel")
            self._no_files = True
            self.wheel.release()

    def _on_closed(self, reason: str) -> None:
        if self.wheel.owner is not self:
            return
        log.info("wheel closed: %s", reason)
        if self._dragging:
            self._used = True

    def _on_chosen(self, wheel: str, item_id: str, paths: list) -> None:
        if self.wheel.owner is not self:
            return  # a wheel opened without a drag (Home, Nautilus menu)
        op: Operation = self.registry.get(item_id)
        selection = self._selection
        if paths and (selection is None or tuple(paths) != self._paths):
            selection = Selection.from_paths(paths)
        if selection is None:
            log.warning("chose %s but the dragged files are unknown", item_id)
            return
        log.info("chosen %s for %s", op.id, [str(i.path) for i in selection.items])
        if paths:
            self._dropped = True
            if not self._dragging:  # the drop was handled after the drag had ended
                session = self._session
                QTimer.singleShot(0, self, lambda: self._release(session))
        self.operation_chosen.emit(op, selection)


def _describe(content: WheelContent) -> str:
    if not content.items:
        return f"empty ({content.empty_message})"
    return ", ".join(item.label for item in content.items)
