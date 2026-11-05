"""A wheel for files chosen without a drag: Home, the Nautilus menu and ``quince open``.

The service has one wheel window; the drag controller and this session share it, and each
one only reacts to the wheel while it is the wheel's ``owner``.

1. ``open(paths)`` detects the file types and queries the registry on a worker thread.
2. The wheel opens at the given point (or the pointer) with the requested wheel, or the first
   one that has items: Convert, then Tools, then Actions (Decision Log 2026-11-05). It takes
   the keyboard and the pointer, so arrows / Enter / Esc and clicks work, and a click outside
   closes it.
3. Trigger taps (the keys from the config) switch between the wheels.
4. A choice is emitted as ``operation_chosen`` once the wheel has closed and let go of the
   keyboard and the pointer, so a dialog of the operation can take them.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Signal
from PySide6.QtGui import QCursor

from quince.core import config, deps
from quince.core.operations import Operation
from quince.core.registry import Registry
from quince.core.selection import Selection
from quince.input.taps import TapDetector
from quince.ui.keys import keysym_name
from quince.ui.wheel.model import WHEEL_TITLES, WheelContent, build_contents
from quince.ui.wheel.window import WheelWindow

log = logging.getLogger(__name__)


def first_wheel(contents: Mapping[str, WheelContent]) -> str:
    """The first wheel with items (Convert, Tools, Actions), else Convert."""
    return next((w for w in WHEEL_TITLES if contents[w].items), "convert")


class DirectWheel(QObject):
    operation_chosen = Signal(object, object)  # Operation, Selection
    # Worker thread -> GUI thread: request number, selection, contents, tap detector.
    _resolved = Signal(int, object, object, object)

    def __init__(
        self,
        wheel: WheelWindow,
        registry: Registry,
        *,
        load_config: Callable[[], config.Config] = config.load,
        probe: Callable[[], deps.Availability] = deps.Availability.probe,
        submit: Callable[[Callable[[], None]], object] | None = None,
        busy: Callable[[], bool] = lambda: False,
        notify: Callable[[str], None] = lambda _text: None,
    ) -> None:
        super().__init__()
        self.wheel = wheel
        self.registry = registry
        self._load_config = load_config
        self._probe = probe
        if submit is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quince-open")
            submit = self._executor.submit
        self._submit = submit
        self._busy = busy  # a drag is in progress: the drag owns the wheel
        self._notify = notify

        self._request = 0
        self._where: tuple[str | None, QPointF | None] = (None, None)
        self.selection: Selection | None = None
        self.contents: Mapping[str, WheelContent] | None = None
        self._taps = TapDetector()
        self._chosen: tuple[Operation, Selection] | None = None

        self._resolved.connect(self._on_resolved)
        wheel.chosen.connect(self._on_chosen)
        wheel.closed.connect(self._on_closed)
        wheel.installEventFilter(self)

    @property
    def is_open(self) -> bool:
        return self.wheel.is_open and self.wheel.owner is self

    def open(
        self,
        paths: Sequence[str | Path],
        *,
        wheel: str | None = None,
        pos: QPoint | QPointF | None = None,
    ) -> bool:
        """Open a wheel for ``paths``. False when none of them exists."""
        if wheel is not None and wheel not in WHEEL_TITLES:
            raise ValueError(f"unknown wheel {wheel!r}")
        existing = [str(p) for p in paths if os.path.lexists(p)]
        if not existing:
            self._notify("Quince: the files no longer exist")
            return False
        self._request += 1
        request = self._request
        self._where = (wheel, None if pos is None else QPointF(pos))

        def work() -> None:
            try:
                cfg = self._load_config()
                selection = Selection.from_paths(existing)
                contents = build_contents(selection, self.registry, self._probe(), cfg)
            except Exception:
                log.exception("could not read the files for the wheel")
                return
            taps = TapDetector(cfg.triggers.keys, max_tap_ms=cfg.triggers.max_tap_ms)
            self._resolved.emit(request, selection, contents, taps)

        self._submit(work)
        return True

    def close(self) -> None:
        if self.is_open:
            self.wheel.close_wheel("closed")

    # --- internals ---------------------------------------------------------------

    def _on_resolved(self, request: int, selection: Selection, contents, taps) -> None:
        if request != self._request:
            return  # a newer request
        if self._busy():
            log.info("a drag is in progress; not opening a wheel for %s", selection.describe())
            return
        wheel, pos = self._where
        self.selection, self.contents, self._taps = selection, contents, taps
        name = wheel or first_wheel(contents)
        point = pos if pos is not None else QPointF(QCursor.pos())
        self._chosen = None
        self.wheel.open_at(point, contents[name], grab=True, owner=self)
        log.info("wheel %s opened for %s", name, selection.describe())

    def switch(self, wheel: str) -> None:
        if self.is_open and self.contents is not None:
            self.wheel.switch_to(self.contents[wheel])
            log.info("wheel switched to %s", wheel)

    def _on_chosen(self, wheel: str, item_id: str, paths: list) -> None:
        if self.wheel.owner is not self or self.selection is None:
            return
        self._chosen = (self.registry.get(item_id), self.selection)

    def _on_closed(self, reason: str) -> None:
        if self.wheel.owner is not self:
            return
        self._taps.reset()
        chosen, self._chosen = self._chosen, None
        if chosen is not None:
            op, selection = chosen
            log.info("chosen %s for %s", op.id, [str(i.path) for i in selection.items])
            self.operation_chosen.emit(op, selection)
        else:
            log.info("wheel closed: %s", reason)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is not self.wheel or not self.is_open:
            return False
        kind = event.type()
        if kind == QEvent.Type.KeyPress and not event.isAutoRepeat():
            self._taps.press(keysym_name(event), event.timestamp())
        elif kind == QEvent.Type.KeyRelease and not event.isAutoRepeat():
            tapped = self._taps.release(keysym_name(event), event.timestamp())
            if tapped is not None:
                self.switch(tapped)
        elif kind == QEvent.Type.MouseButtonPress:
            self._taps.button()
        return False
