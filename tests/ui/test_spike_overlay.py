import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, QRect, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from quince.ui.spike.overlay import DIAMETER, DROP_SETTLE_MS, DebugOverlay, clamp_to_screen

SCREEN = QRect(0, 0, 1920, 1080)


@pytest.mark.parametrize(
    ("center", "expected"),
    [
        (QPoint(500, 500), QPoint(500 - DIAMETER // 2, 500 - DIAMETER // 2)),
        (QPoint(5, 5), QPoint(0, 0)),
        (QPoint(1919, 1079), QPoint(1920 - DIAMETER, 1080 - DIAMETER)),
    ],
)
def test_clamp_to_screen(center, expected):
    assert clamp_to_screen(center, DIAMETER, SCREEN) == expected


def test_clamp_respects_screen_offset():
    # second monitor right of the first, as on the development machine
    screen = QRect(1920, 0, 2560, 1440)
    assert clamp_to_screen(QPoint(1930, 10), DIAMETER, screen) == QPoint(1920, 0)


def _mime(paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    return mime


@pytest.mark.parametrize("proposed", [Qt.DropAction.MoveAction, Qt.DropAction.CopyAction])
def test_drop_is_always_copy(qtbot, proposed):
    overlay = DebugOverlay()
    qtbot.addWidget(overlay)
    overlay.show_at(QPoint(300, 300), "convert")
    mime = _mime(["/tmp/a.png", "/tmp/b.png"])
    actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
    modifiers = Qt.KeyboardModifier.ShiftModifier  # Shift held at drop time

    enter = QDragEnterEvent(QPoint(10, 10), actions, mime, Qt.MouseButton.LeftButton, modifiers)
    QApplication.sendEvent(overlay, enter)
    assert enter.isAccepted()
    assert enter.dropAction() == Qt.DropAction.CopyAction

    drop = QDropEvent(QPointF(10, 10), actions, mime, Qt.MouseButton.LeftButton, modifiers)
    drop.setDropAction(proposed)
    with qtbot.waitSignal(overlay.dropped) as blocker:
        QApplication.sendEvent(overlay, drop)
    assert drop.isAccepted()
    assert drop.dropAction() == Qt.DropAction.CopyAction
    assert blocker.args == ["convert", ["/tmp/a.png", "/tmp/b.png"]]
    assert not overlay.isVisible()


def test_drag_without_urls_is_rejected(qtbot):
    overlay = DebugOverlay()
    qtbot.addWidget(overlay)
    mime = QMimeData()
    mime.setText("just text")
    enter = QDragEnterEvent(
        QPoint(1, 1),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(overlay, enter)
    assert not enter.isAccepted()


def test_show_at_sets_wheel_and_is_visible(qtbot):
    overlay = DebugOverlay()
    qtbot.addWidget(overlay)
    overlay.show_at(QPoint(200, 200), "tools")
    assert overlay.isVisible()
    assert overlay.wheel == "tools"
    overlay.close_overlay("test")
    assert not overlay.isVisible()


def _drop(overlay, paths):
    mime = _mime(paths)
    actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
    args = (actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(overlay, QDragEnterEvent(QPoint(5, 5), *args))
    drop = QDropEvent(QPointF(5, 5), *args)
    QApplication.sendEvent(overlay, drop)
    return drop


def test_cancel_then_drop_is_swallowed(qtbot):
    overlay = DebugOverlay()
    qtbot.addWidget(overlay)
    overlay.show_at(QPoint(300, 300), "actions")
    overlay.cancel("right click")
    assert overlay.isVisible(), "stays mapped to swallow the drop"
    assert overlay.cancelled
    with qtbot.assertNotEmitted(overlay.dropped, wait=DROP_SETTLE_MS * 3):
        drop = _drop(overlay, ["/tmp/a.png"])
        assert drop.isAccepted()
        assert drop.dropAction() == Qt.DropAction.CopyAction
    assert not overlay.isVisible()
    assert not overlay.cancelled


def test_cancel_arriving_just_after_drop_still_wins(qtbot):
    overlay = DebugOverlay()
    qtbot.addWidget(overlay)
    overlay.show_at(QPoint(300, 300), "actions")
    with qtbot.assertNotEmitted(overlay.dropped, wait=DROP_SETTLE_MS * 3):
        _drop(overlay, ["/tmp/a.png"])
        overlay.cancel("right click")  # raw event delivered after the XDND drop
    assert not overlay.isVisible()


def test_reopening_clears_cancel(qtbot):
    overlay = DebugOverlay()
    qtbot.addWidget(overlay)
    overlay.show_at(QPoint(300, 300), "tools")
    overlay.cancel("right click")
    overlay.close_overlay("drag ended")
    overlay.show_at(QPoint(300, 300), "tools")
    assert not overlay.cancelled
    with qtbot.waitSignal(overlay.dropped):
        _drop(overlay, ["/tmp/a.png"])
