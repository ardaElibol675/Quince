"""The wheel window: frameless, translucent, always on top, centered on a point.

It is independent of how it is opened. The service drives it from the global drag
watcher; ``quince wheel-demo`` drives it directly. Keys arrive either as Qt key events (when
the window has the keyboard) or through ``handle_key`` with X keysym names (raw X11 events).

Drag sessions (``hold=True`` in ``open_at``): a drag source may still drop after the wheel
closed (GTK drops on Enter and on right or middle clicks). So a closed wheel stays mapped,
paints nothing and swallows drops as Copy until ``release()`` is called at the end of the
drag. Drops are acted on after ``drop_settle_ms``, because a cancel click (raw X11 input)
and the drop it causes (XDND) can arrive in either order.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
)
from PySide6.QtWidgets import QWidget

from quince.ui.theme.colors import wheel_colors
from quince.ui.wheel import geometry
from quince.ui.wheel.geometry import WheelLayout
from quince.ui.wheel.model import WheelContent
from quince.ui.wheel.render import WheelPainter
from quince.ui.wheel.state import KeyResult, WheelState

log = logging.getLogger(__name__)

OPEN_MS = 170
DROP_SETTLE_MS = 80
CLOSE_MS = 110
SWITCH_MS = 200
OPEN_SCALE = 0.86
SWITCH_ROTATION = 24.0
SWITCH_SCALE = 0.92
# The wheel center keeps this distance (beyond the ring) from the screen edges.
SCREEN_PADDING = 8.0

QT_KEYS = {
    Qt.Key.Key_Left: "Left",
    Qt.Key.Key_Right: "Right",
    Qt.Key.Key_Up: "Up",
    Qt.Key.Key_Down: "Down",
    Qt.Key.Key_Home: "Home",
    Qt.Key.Key_End: "End",
    Qt.Key.Key_Return: "Return",
    Qt.Key.Key_Enter: "KP_Enter",
    Qt.Key.Key_Space: "space",
    Qt.Key.Key_Escape: "Escape",
    Qt.Key.Key_Tab: "Tab",
    Qt.Key.Key_Backtab: "Tab",
}


def local_paths(event: QDropEvent | QDragEnterEvent) -> list[str]:
    mime = event.mimeData()
    if mime is None or not mime.hasUrls():
        return []
    return [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]


class WheelWindow(QWidget):
    chosen = Signal(str, str, list)  # wheel, item id, dropped paths ([] without a drop)
    closed = Signal(str)  # reason; also sent after ``chosen`` (reason "chosen")
    highlight_changed = Signal(object)  # item id or None
    drag_entered = Signal(list)  # local file paths the entering drag carries

    def __init__(
        self,
        *,
        scheme: str = "system",
        animations: bool = True,
        grab_keyboard: bool = False,
        drop_settle_ms: int = DROP_SETTLE_MS,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.X11BypassWindowManagerHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)

        self.scheme = scheme
        self.animations = animations
        self.grab_keyboard_on_open = grab_keyboard
        self.drop_settle_ms = drop_settle_ms
        self.colors = wheel_colors(scheme)
        self.state = WheelState()
        self.content = WheelContent("convert")
        self.layout_ = geometry.layout(0)
        self._painter = WheelPainter()
        self._center = QPointF()  # global position of the wheel center
        self._open = False
        self._hold = False  # keep mapped after closing, to swallow a late drop
        # Paths read at drag enter. Reading them again at drop is a second round trip to the
        # drag source, which blocks the GUI thread (up to Qt's 5 s timeout) if it stalls.
        self._drag_paths: list[str] = []

        self._progress = 0.0  # open/close animation, 0 = closed, 1 = open
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_progress)
        self._anim.finished.connect(self._on_anim_finished)

        self._switch = 1.0  # switch animation, 0 = old contents, 1 = new contents
        self._previous: tuple[WheelContent, WheelLayout, int | None] | None = None
        self._switch_anim = QVariantAnimation(self)
        self._switch_anim.setDuration(SWITCH_MS)
        self._switch_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._switch_anim.valueChanged.connect(self._on_switch)
        self._switch_anim.finished.connect(self._on_switch_finished)

        app = QGuiApplication.instance()
        if app is not None:
            app.styleHints().colorSchemeChanged.connect(self._on_color_scheme)

    # --- public API ---------------------------------------------------------

    @property
    def is_open(self) -> bool:
        """True from ``open_at`` until the wheel is closed (also while it animates in)."""
        return self._open

    @property
    def highlighted_item(self) -> str | None:
        index = self.state.highlighted
        return None if index is None else self.content.items[index].id

    @property
    def center(self) -> QPointF:
        """Global position of the wheel center (after keeping the wheel on screen)."""
        return QPointF(self._center)

    @property
    def holding(self) -> bool:
        """Closed but still mapped, swallowing drops until ``release()``."""
        return self._hold and not self._open and self.isVisible()

    def open_at(self, pos: QPoint | QPointF, content: WheelContent, *, hold: bool = False) -> None:
        """Show ``content`` centered on the global point ``pos`` (kept inside the screen).

        With ``hold``, the window stays mapped after closing until ``release()``.
        """
        self._hold = hold
        self._set_content(content)
        self._previous = None
        self._switch = 1.0
        self._center = QPointF(pos)
        self._place()
        self._open = True
        self._drag_paths = []
        self.show()
        self.raise_()
        if self.grab_keyboard_on_open:
            self.grabKeyboard()
        self._animate(1.0, OPEN_MS, QEasingCurve.Type.OutCubic)
        log.debug("wheel %s opened with %d items", content.wheel, len(content.items))

    def set_content(self, content: WheelContent) -> None:
        """Replace the contents without animation (e.g. the dragged files became known)."""
        if not self._open or content == self.content:
            return
        self._set_content(content)
        self._place()

    def switch_to(self, content: WheelContent) -> None:
        """Replace the contents in place (another trigger was tapped)."""
        if not self._open:
            return
        if content == self.content:
            return
        self._previous = (self.content, self.layout_, self.state.highlighted)
        self._set_content(content)
        self._place()
        if self.animations:
            self._switch_anim.stop()
            self._switch_anim.setStartValue(0.0)
            self._switch_anim.setEndValue(1.0)
            self._switch_anim.start()
        else:
            self._on_switch_finished()
        log.debug("wheel switched to %s", content.wheel)

    def close_wheel(self, reason: str) -> None:
        if not self._open:
            return
        self._open = False
        if self.grab_keyboard_on_open:
            self.releaseKeyboard()
        log.debug("wheel closed: %s", reason)
        self.closed.emit(reason)
        self._animate(0.0, CLOSE_MS, QEasingCurve.Type.InCubic)

    def release(self) -> None:
        """End of the drag: stop holding. Closes the wheel first if it is still open."""
        self._hold = False
        if self._open:
            self.close_wheel("drag ended")
        elif self._progress <= 0.0 or not self.animations:
            self.hide()

    def handle_key(self, key: str) -> bool:
        """Handle a key by X keysym name. Returns False if the wheel does not use it."""
        if not self._open:
            return False
        result = self.state.key(key)
        if result is KeyResult.MOVED:
            self._highlight_updated()
        elif result is KeyResult.CHOOSE:
            self._choose(self.state.highlighted, [])
        elif result is KeyResult.CLOSE:
            self.close_wheel(key.lower())
        return result is not KeyResult.IGNORED

    def pointer_at(self, local: QPointF) -> None:
        """The pointer (or a drag) is at ``local`` window coordinates."""
        rel = local - self._local_center()
        index = geometry.hit_test(self.layout_, rel.x(), rel.y())
        if self.state.pointer((rel.x(), rel.y()), index):
            self._highlight_updated()

    def hit(self, local: QPointF) -> int | None:
        rel = local - self._local_center()
        return geometry.hit_test(self.layout_, rel.x(), rel.y())

    # --- internals ----------------------------------------------------------

    def _set_content(self, content: WheelContent) -> None:
        self.content = content
        self.layout_ = geometry.layout(len(content.items))
        self.state.reset(len(content.items))
        self.highlight_changed.emit(None)

    def _place(self) -> None:
        layouts = [self.layout_]
        if self._previous is not None:
            layouts.append(self._previous[1])
        size = max(lay.window_size for lay in layouts)
        radius = max(lay.outer_radius for lay in layouts) + SCREEN_PADDING
        screen = QGuiApplication.screenAt(self._center.toPoint()) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            cx, cy = geometry.clamp_center(
                self._center.x(),
                self._center.y(),
                radius,
                (area.x(), area.y(), area.width(), area.height()),
            )
            self._center = QPointF(cx, cy)
        self.setFixedSize(size, size)
        self.move(round(self._center.x() - size / 2), round(self._center.y() - size / 2))
        self.update()

    def _local_center(self) -> QPointF:
        return QPointF(self.width() / 2, self.height() / 2)

    def _animate(self, target: float, duration: int, curve: QEasingCurve.Type) -> None:
        self._anim.stop()
        if not self.animations:
            self._on_progress(target)
            self._on_anim_finished()
            return
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(curve)
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(target)
        self._anim.start()

    def _on_progress(self, value: float) -> None:
        self._progress = float(value)
        self.update()

    def _on_anim_finished(self) -> None:
        if not self._open and self._progress <= 0.0 and not self._hold:
            self.hide()

    def _on_switch(self, value: float) -> None:
        self._switch = float(value)
        self.update()

    def _on_switch_finished(self) -> None:
        self._switch = 1.0
        self._previous = None
        if self._open:
            self._place()

    def _on_color_scheme(self, *_args) -> None:
        if self.scheme == "system":
            self.colors = wheel_colors("system")
            self.update()

    def _highlight_updated(self) -> None:
        self.update()
        self.highlight_changed.emit(self.highlighted_item)

    def _choose(self, index: int | None, paths: list[str]) -> None:
        if index is None or not 0 <= index < len(self.content.items):
            return
        item = self.content.items[index]
        log.debug("wheel %s: chose %s with %d path(s)", self.content.wheel, item.id, len(paths))
        self.chosen.emit(self.content.wheel, item.id, paths)
        self.close_wheel("chosen")

    # --- events -------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        center = self._local_center()
        open_scale = OPEN_SCALE + (1 - OPEN_SCALE) * self._progress
        s = self._switch
        if self._previous is not None and s < 1.0:
            content, lay, highlighted = self._previous
            self._painter.paint(
                painter, center, lay, content, highlighted, self.colors,
                opacity=self._progress * (1 - s),
                scale=open_scale * (1 - (1 - SWITCH_SCALE) * s),
                rotation=-SWITCH_ROTATION * s,
            )  # fmt: skip
        self._painter.paint(
            painter, center, self.layout_, self.content, self.state.highlighted, self.colors,
            opacity=self._progress * s,
            scale=open_scale * (SWITCH_SCALE + (1 - SWITCH_SCALE) * s),
            rotation=SWITCH_ROTATION * (1 - s),
        )  # fmt: skip

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._open:
            self.pointer_at(event.position())

    def leaveEvent(self, event) -> None:
        if self._open and self.state.pointer_gone():
            self._highlight_updated()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._open and event.button() in (
            Qt.MouseButton.RightButton,
            Qt.MouseButton.MiddleButton,
        ):
            self.close_wheel("cancel click")

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if not self._open or event.button() != Qt.MouseButton.LeftButton:
            return
        index = self.hit(event.position())
        if index is None:
            self.close_wheel("click outside slices")
        else:
            self._choose(index, [])

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = QT_KEYS.get(Qt.Key(event.key()))
        if key is None or not self.handle_key(key):
            super().keyPressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if not self._open:
            event.ignore()
            return
        if not event.mimeData().hasUrls():
            event.ignore()
            self.drag_entered.emit([])  # not a file drag (text, links, ...)
            return
        # Always COPY: the drag source must never delete or move the original.
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self._drag_paths = local_paths(event)
        self.pointer_at(event.position())
        self.drag_entered.emit(list(self._drag_paths))

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if self._open:
            self.pointer_at(event.position())

    def dragLeaveEvent(self, event) -> None:
        if self._open and self.state.pointer_gone():
            self._highlight_updated()

    def dropEvent(self, event: QDropEvent) -> None:
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if not self._open:
            log.debug("drop swallowed: wheel already closed")
            return
        paths, self._drag_paths = self._drag_paths or local_paths(event), []
        # The highlighted slice, not the one under the pointer: in keyboard mode the
        # pointer position is ignored (Enter drops wherever the pointer is).
        index = self.state.highlighted
        if self.drop_settle_ms > 0:
            QTimer.singleShot(self.drop_settle_ms, lambda: self._finish_drop(index, paths))
        else:
            self._finish_drop(index, paths)

    def _finish_drop(self, index: int | None, paths: list[str]) -> None:
        if not self._open:
            log.debug("drop ignored: wheel closed while the drop settled")
            return
        if index is None:
            self.close_wheel("dropped outside slices")
        else:
            self._choose(index, paths)
