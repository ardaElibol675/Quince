"""``quince spike-drag``: wire the X11 watcher to the debug overlay."""

from __future__ import annotations

import logging
import signal
import sys

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from quince.input import drag
from quince.input.x11 import X11InputWatcher, X11Unavailable
from quince.ui.spike.overlay import DebugOverlay

log = logging.getLogger(__name__)

# After the drag ends, wait this long for the drop event before closing the overlay.
DROP_GRACE_MS = 400


class _Bridge(QObject):
    """Carries watcher signals from the X11 thread to the GUI thread (queued)."""

    received = Signal(object)


class SpikeController(QObject):
    def __init__(self, overlay: DebugOverlay) -> None:
        super().__init__()
        self.overlay = overlay
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.setInterval(DROP_GRACE_MS)
        self._close_timer.timeout.connect(lambda: self.overlay.close_overlay("drag ended"))

    def handle(self, sig: drag.Signal) -> None:
        if isinstance(sig, drag.DragStarted):
            log.info("drag started")
        elif isinstance(sig, drag.TriggerTapped):
            log.info("tap %s -> %s", sig.key, sig.wheel)
            self._close_timer.stop()
            if self.overlay.isVisible():
                self.overlay.wheel = sig.wheel
                self.overlay.update()
            else:
                self.overlay.show_at(QCursor.pos(), sig.wheel)
        elif isinstance(sig, drag.DragKey):
            log.info("key during drag: %s", sig.key)
            if sig.key == "Escape":
                self.overlay.close_overlay("escape")
        elif isinstance(sig, drag.DragCancel):
            log.info("button %d during drag: cancel", sig.button)
            self.overlay.cancel(f"button {sig.button}")
        elif isinstance(sig, drag.DragEnded):
            log.info("drag ended")
            self._close_timer.start()


def run(*, max_tap_ms: int, require_dnd: bool) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)

    overlay = DebugOverlay()
    controller = SpikeController(overlay)
    bridge = _Bridge()
    bridge.received.connect(controller.handle)

    tracker = drag.DragTracker(max_tap_ms=max_tap_ms, require_dnd=require_dnd)
    watcher = X11InputWatcher(tracker, bridge.received.emit)
    try:
        watcher.start()
    except X11Unavailable as exc:
        log.error("%s", exc)
        return 2

    # Let Ctrl+C in the terminal stop the Qt loop.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    tick = QTimer()
    tick.start(200)
    tick.timeout.connect(lambda: None)

    log.info(
        "spike-drag running (tap <= %d ms, require XDND: %s). Ctrl+C to quit.",
        max_tap_ms,
        require_dnd,
    )
    try:
        return app.exec()
    finally:
        watcher.stop()
