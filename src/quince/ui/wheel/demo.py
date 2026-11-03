"""``quince wheel-demo``: preview the wheel with any number of slices.

The wheel opens at the mouse pointer and takes the keyboard. Tap Shift / Ctrl / Alt to
switch between Convert / Tools / Actions, Q to quit. After a choice or close it opens
again at the pointer. With ``grab=False`` the keyboard is left alone, so a file manager
can start a drag and drop onto the wheel.
"""

from __future__ import annotations

import logging
import signal
import sys
from collections.abc import Sequence
from itertools import cycle, islice
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from quince.core import formats
from quince.ui.wheel.model import WHEEL_TITLES, WheelContent, WheelItem
from quince.ui.wheel.window import WheelWindow

log = logging.getLogger(__name__)

REOPEN_MS = 700
SWITCH_KEYS = {
    Qt.Key.Key_Shift: "convert",
    Qt.Key.Key_Control: "tools",
    Qt.Key.Key_Alt: "actions",
}

_TOOLS = (
    ("Compress", "package-x-generic"),
    ("Crop", "edit-cut"),
    ("Resize", "zoom-fit-best"),
    ("Rotate", "object-rotate-right"),
    ("Metadata", "document-properties"),
    ("Trim", "media-playback-start"),
    ("Remove Audio", "audio-volume-muted"),
    ("Normalize", "audio-volume-high"),
    ("Annotate", "document-edit"),
    ("Redact", "edit-clear"),
    ("Frame", "insert-image"),
    ("Collage", "view-grid"),
    ("Merge PDFs", "document-new"),
    ("Split PDF", "edit-cut"),
    ("Extract", "folder-open"),
    ("Read QR Code", "camera-photo"),
)
_ACTIONS = (
    ("Copy", "edit-copy"),
    ("Move", "go-jump"),
    ("Copy Path", "edit-paste"),
    ("Open in Terminal", "utilities-terminal"),
    ("Show in Files", "system-file-manager"),
    ("Open With", "document-open"),
    ("Float", "view-pin"),
)


def demo_content(wheel: str, count: int) -> WheelContent:
    """Fake contents with ``count`` items; names repeat with a number past the pool."""
    if wheel == "convert":
        pool = [(f.label, f.name, f.category.icon) for f in formats.all_formats()]
    else:
        names = _TOOLS if wheel == "tools" else _ACTIONS
        pool = [(label, label, icon) for label, icon in names]
    items = []
    for i, (label, title, icon) in enumerate(islice(cycle(pool), count)):
        rnd = i // len(pool)
        suffix = f" {rnd + 1}" if rnd else ""
        items.append(WheelItem(f"demo.{wheel}.{i}", label + suffix, title + suffix, icon))
    return WheelContent(wheel, tuple(items))


def file_contents(paths: Sequence[str]) -> dict[str, WheelContent]:
    """Real registry contents for these files, as Phase 4 will show them."""
    from quince.core import config, deps
    from quince.core.operations import Kind
    from quince.core.registry import discover
    from quince.core.selection import Selection
    from quince.ui.wheel.model import content_from_operations

    selection = Selection.from_paths(Path(p).absolute() for p in paths)
    registry, available, cfg = discover(), deps.Availability.probe(), config.load()
    contents = {}
    for wheel in WHEEL_TITLES:
        kind = Kind.from_wheel(wheel)
        ops = registry.query(selection, kind, available=available, prefs=cfg.wheel(wheel))
        message = "Nothing available"
        if kind is not Kind.ACTION and not selection.is_uniform:
            message = "Only file actions for folders and mixed selections"
        contents[wheel] = content_from_operations(wheel, ops, message)
    return contents


class _DemoKeys(QObject):
    """Modifier taps switch wheels; Q quits."""

    def __init__(self, demo: Demo) -> None:
        super().__init__()
        self.demo = demo
        self._pending: Qt.Key | None = None

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            key = Qt.Key(event.key())
            if event.isAutoRepeat():
                return False
            self._pending = key if key in SWITCH_KEYS and self._pending is None else None
            if key == Qt.Key.Key_Q:
                QApplication.quit()
                return True
        elif event.type() == QEvent.Type.KeyRelease and not event.isAutoRepeat():
            key = Qt.Key(event.key())
            if key == self._pending:
                self.demo.switch(SWITCH_KEYS[key])
            self._pending = None
        return False


class Demo:
    def __init__(
        self, contents: dict[str, WheelContent], wheel: str, *, once: bool, grab: bool, **kw
    ):
        self.contents = contents
        self.current = wheel
        self.once = once
        self.window = WheelWindow(grab_keyboard=grab, **kw)
        self.window.chosen.connect(self._on_chosen)
        self.window.closed.connect(self._on_closed)
        self.window.highlight_changed.connect(lambda item: log.info("highlight: %s", item))
        self._keys = _DemoKeys(self)
        self.window.installEventFilter(self._keys)

    def open(self) -> None:
        self.window.open_at(QCursor.pos(), self.contents[self.current])
        log.info("%s wheel: %d item(s)", self.current, len(self.contents[self.current].items))

    def switch(self, wheel: str) -> None:
        if wheel != self.current:
            self.current = wheel
            self.window.switch_to(self.contents[wheel])
            log.info("switched to %s (%d item(s))", wheel, len(self.contents[wheel].items))

    def _on_chosen(self, wheel: str, item: str, paths: list) -> None:
        log.info("CHOSEN %s / %s %s", wheel, item, paths or "")

    def _on_closed(self, reason: str) -> None:
        log.info("closed: %s", reason)
        if self.once:
            QTimer.singleShot(300, QApplication.quit)
        else:
            QTimer.singleShot(REOPEN_MS, self.open)


def run(
    *,
    count: int,
    wheel: str,
    files: Sequence[str] = (),
    scheme: str = "system",
    animations: bool = True,
    once: bool = False,
    grab: bool = True,
) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    contents = file_contents(files) if files else {w: demo_content(w, count) for w in WHEEL_TITLES}
    demo = Demo(contents, wheel, once=once, grab=grab, scheme=scheme, animations=animations)
    QTimer.singleShot(0, demo.open)

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    tick = QTimer()
    tick.start(200)
    tick.timeout.connect(lambda: None)  # lets Python see Ctrl+C while Qt runs
    log.info("wheel-demo: Shift/Ctrl/Alt tap = switch wheel, arrows/Enter/Esc, Q = quit")
    return app.exec()
