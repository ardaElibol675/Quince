from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QImage, QPainter

from quince.core import formats
from quince.core.operations import Kind
from quince.core.registry import discover
from quince.core.selection import Item, Selection
from quince.ui.theme.colors import DARK, LIGHT, wheel_colors
from quince.ui.wheel import geometry
from quince.ui.wheel.demo import demo_content
from quince.ui.wheel.model import content_from_operations
from quince.ui.wheel.render import WheelPainter
from quince.ui.wheel.window import WheelWindow


@pytest.fixture
def wheel(qtbot):
    w = WheelWindow(animations=False)
    qtbot.addWidget(w)
    events = {"chosen": [], "closed": []}
    w.chosen.connect(lambda *args: events["chosen"].append(args))
    w.closed.connect(events["closed"].append)
    w.events = events
    return w


def _slice_point(w: WheelWindow, index: int) -> QPoint:
    s = w.layout_.slices[index]
    return QPoint(round(w.width() / 2 + s.label_x), round(w.height() / 2 + s.label_y))


def test_opens_centered_with_size_from_slice_count(wheel):
    middle = wheel.screen().availableGeometry().center()
    wheel.open_at(middle, demo_content("convert", 7))
    assert wheel.isVisible() and wheel.is_open
    assert wheel.width() == geometry.layout(7).window_size
    assert wheel.geometry().center().x() == pytest.approx(middle.x(), abs=1)
    assert wheel.geometry().center().y() == pytest.approx(middle.y(), abs=1)
    wheel.switch_to(demo_content("convert", 3))
    assert wheel.width() == geometry.layout(3).window_size


def test_stays_inside_the_screen(wheel):
    wheel.open_at(QPoint(0, 0), demo_content("tools", 9))
    area = wheel.screen().availableGeometry()
    center = wheel.geometry().center()
    radius = wheel.layout_.outer_radius
    assert center.x() - radius >= area.left() - 1
    assert center.y() - radius >= area.top() - 1


def test_keyboard_navigation_and_choose(wheel, qtbot):
    content = demo_content("actions", 5)
    wheel.open_at(QPoint(400, 300), content)
    qtbot.keyClick(wheel, Qt.Key.Key_Right)
    qtbot.keyClick(wheel, Qt.Key.Key_Right)
    assert wheel.highlighted_item == content.items[1].id
    qtbot.keyClick(wheel, Qt.Key.Key_End)
    qtbot.keyClick(wheel, Qt.Key.Key_Left)
    assert wheel.highlighted_item == content.items[3].id
    qtbot.keyClick(wheel, Qt.Key.Key_Return)
    assert wheel.events["chosen"] == [("actions", content.items[3].id, [])]
    assert wheel.events["closed"] == ["chosen"]
    assert not wheel.is_open and not wheel.isVisible()


@pytest.mark.parametrize(
    ("key", "reason"), [(Qt.Key.Key_Escape, "escape"), (Qt.Key.Key_Tab, "tab")]
)
def test_escape_and_tab_close(wheel, qtbot, key, reason):
    wheel.open_at(QPoint(400, 300), demo_content("convert", 3))
    qtbot.keyClick(wheel, key)
    assert wheel.events["closed"] == [reason] and wheel.events["chosen"] == []


def test_raw_keysyms_drive_the_same_logic(wheel):
    content = demo_content("convert", 4)
    wheel.open_at(QPoint(400, 300), content)
    assert wheel.handle_key("Home") and wheel.highlighted_item == content.items[0].id
    assert not wheel.handle_key("a")
    assert wheel.handle_key("space")
    assert wheel.events["chosen"][0][1] == content.items[0].id
    assert not wheel.handle_key("Right")  # closed


def test_mouse_hover_and_click(wheel, qtbot):
    content = demo_content("tools", 6)
    wheel.open_at(QPoint(400, 300), content)
    qtbot.mouseMove(wheel, _slice_point(wheel, 2))
    wheel.pointer_at(QPointF(_slice_point(wheel, 2)))
    assert wheel.highlighted_item == content.items[2].id
    qtbot.mouseClick(wheel, Qt.MouseButton.LeftButton, pos=_slice_point(wheel, 2))
    assert wheel.events["chosen"] == [("tools", content.items[2].id, [])]


def test_click_in_center_closes(wheel, qtbot):
    wheel.open_at(QPoint(400, 300), demo_content("tools", 6))
    qtbot.mouseClick(wheel, Qt.MouseButton.LeftButton, pos=wheel.rect().center())
    assert wheel.events["closed"] == ["click outside slices"] and not wheel.events["chosen"]


def test_right_click_cancels(wheel, qtbot):
    wheel.open_at(QPoint(400, 300), demo_content("tools", 6))
    qtbot.mouseClick(wheel, Qt.MouseButton.RightButton, pos=_slice_point(wheel, 1))
    assert wheel.events["closed"] == ["cancel click"] and not wheel.events["chosen"]


def test_switch_in_place_resets_highlight(wheel):
    wheel.open_at(QPoint(400, 300), demo_content("convert", 7))
    wheel.handle_key("Right")
    center = wheel.geometry().center()
    wheel.switch_to(demo_content("actions", 3))
    assert wheel.content.wheel == "actions" and wheel.highlighted_item is None
    assert wheel.is_open and wheel.geometry().center() == center
    wheel.handle_key("Right")
    assert wheel.highlighted_item == "demo.actions.0"


def test_empty_wheel(wheel):
    wheel.open_at(QPoint(400, 300), demo_content("tools", 0))
    assert not wheel.handle_key("Right") and wheel.highlighted_item is None
    assert wheel.handle_key("Escape")


def _drag_events(pos: QPointF, paths):
    """Enter, move and drop events as GTK sends them with Shift held: Move proposed."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
    rest = (mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
    enter = QDragEnterEvent(pos.toPoint(), actions, *rest)
    move = QDragMoveEvent(pos.toPoint(), actions, *rest)
    drop = QDropEvent(pos, actions, *rest)
    for event in (enter, move, drop):
        event.setDropAction(Qt.DropAction.MoveAction)
    return mime, enter, move, drop


def test_drop_on_slice_is_copy_and_chooses(wheel):
    content = demo_content("convert", 5)
    wheel.open_at(QPoint(400, 300), content)
    pos = QPointF(_slice_point(wheel, 3))
    _mime, enter, move, drop = _drag_events(pos, ["/tmp/a.png", "/tmp/b.png"])
    wheel.dragEnterEvent(enter)
    wheel.dragMoveEvent(move)
    wheel.dropEvent(drop)
    for event in (enter, move, drop):
        assert event.dropAction() == Qt.DropAction.CopyAction  # never Move (Hard Rule 5)
    assert wheel.events["chosen"] == [
        ("convert", content.items[3].id, ["/tmp/a.png", "/tmp/b.png"])
    ]


def test_drop_uses_keyboard_highlight_not_pointer(wheel):
    content = demo_content("convert", 5)
    wheel.open_at(QPoint(400, 300), content)
    pos = QPointF(_slice_point(wheel, 3))
    _mime, enter, move, drop = _drag_events(pos, ["/tmp/a.png"])
    wheel.dragEnterEvent(enter)
    wheel.handle_key("Home")  # keyboard picks slice 0; pointer stays on slice 3
    wheel.dragMoveEvent(move)
    wheel.dropEvent(drop)
    assert wheel.events["chosen"][0][1] == content.items[0].id


def test_drop_in_center_closes(wheel):
    wheel.open_at(QPoint(400, 300), demo_content("convert", 5))
    _mime, enter, move, drop = _drag_events(QPointF(wheel.rect().center()), ["/tmp/a.png"])
    wheel.dragEnterEvent(enter)
    wheel.dragMoveEvent(move)
    wheel.dropEvent(drop)
    assert drop.dropAction() == Qt.DropAction.CopyAction
    assert wheel.events["closed"] == ["dropped outside slices"] and not wheel.events["chosen"]


def test_animated_close_hides_after_animation(qtbot):
    w = WheelWindow(animations=True)
    qtbot.addWidget(w)
    w.open_at(QPoint(400, 300), demo_content("convert", 4))
    qtbot.waitUntil(lambda: w._progress == 1.0, timeout=2000)
    w.switch_to(demo_content("tools", 9))
    w.close_wheel("test")
    assert w.isVisible() and not w.is_open
    qtbot.waitUntil(lambda: not w.isVisible(), timeout=2000)


@pytest.mark.parametrize("colors", [LIGHT, DARK], ids=["light", "dark"])
def test_renders_every_slice_count(colors):
    painter = WheelPainter()
    for n in range(0, 21):
        lay = geometry.layout(n)
        img = QImage(lay.window_size, lay.window_size, QImage.Format.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        content = demo_content(["convert", "tools", "actions"][n % 3], n)
        painter.paint(
            p,
            QPointF(img.width() / 2, img.height() / 2),
            lay,
            content,
            n // 2 if n else None,
            colors,
        )
        p.end()
        center = img.pixelColor(img.width() // 2, img.height() // 2)
        assert center.alpha() > 200  # the center disc is painted


def test_wheel_colors_scheme():
    assert wheel_colors("light") is LIGHT and wheel_colors("dark") is DARK
    assert wheel_colors("system") in (LIGHT, DARK)
    with pytest.raises(ValueError):
        wheel_colors("blue")


def test_demo_content_any_count():
    assert len(demo_content("convert", 0).items) == 0
    big = demo_content("actions", 20)
    assert len(big.items) == 20 and len({i.id for i in big.items}) == 20
    assert len({i.label for i in big.items}) == 20  # repeats get a number


def test_contents_from_registry():
    sel = Selection((Item(Path("/x.png"), format=formats.get("png")),))
    ops = discover().query(sel, Kind.CONVERT)
    content = content_from_operations("convert", ops)
    assert [i.label for i in content.items] == ["JPG", "WebP"]
    assert content.items[0].title == "JPEG image" and content.title == "Convert"


def test_renders_theme_icons(tmp_path):
    """The icon path (not only the monogram fallback) paints without errors."""
    from PySide6.QtGui import QColor, QIcon

    icons = tmp_path / "quince-test" / "32x32" / "apps"
    icons.mkdir(parents=True)
    (tmp_path / "quince-test" / "index.theme").write_text(
        "[Icon Theme]\nName=quince-test\nDirectories=32x32/apps\n\n"
        "[32x32/apps]\nSize=32\nType=Fixed\n"
    )
    icon = QImage(32, 32, QImage.Format.Format_ARGB32)
    icon.fill(QColor(0, 200, 0))
    icon.save(str(icons / "quince-test-icon.png"))

    old_paths, old_name = QIcon.themeSearchPaths(), QIcon.themeName()
    QIcon.setThemeSearchPaths([str(tmp_path)])
    QIcon.setThemeName("quince-test")
    try:
        from quince.ui.wheel.model import WheelContent, WheelItem

        content = WheelContent("tools", (WheelItem("tool.x", "X", "X", "quince-test-icon"),))
        lay = geometry.layout(1)
        img = QImage(lay.window_size, lay.window_size, QImage.Format.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        WheelPainter().paint(
            p, QPointF(img.width() / 2, img.height() / 2), lay, content, None, LIGHT
        )
        p.end()
        s = lay.slices[0]
        assert not QIcon.fromTheme("quince-test-icon").isNull()
        # The icon sits just above the label anchor, the text just below it.
        icon_y = img.height() / 2 + s.label_y - lay.icon_size / 2
        pixel = img.pixelColor(round(img.width() / 2 + s.label_x), round(icon_y))
        assert pixel.green() > 150 and pixel.red() < 100
    finally:
        QIcon.setThemeSearchPaths(old_paths)
        QIcon.setThemeName(old_name)
