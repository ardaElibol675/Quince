"""The editor framework: a window with a toolbar, a preview, an optional side panel and
Save / Cancel, shared by every editor.

An editor subclasses ``EditorWindow`` and

- sets ``name`` (what ``Operation.editor`` refers to) and ``title``;
- implements ``load(selection)`` (runs off the GUI thread before the window opens: decoding),
  ``state()`` / ``set_state()`` (an immutable description of the result, for undo and for
  "changed?"), ``build_job()`` (the background job that writes the result);
- calls ``start_history()`` once its controls exist, and ``commit()`` after every change.

Shared keys: Ctrl+S or Enter saves, Esc closes (asking first when something changed),
Ctrl+Z / Ctrl+Shift+Z (Ctrl+Y) undo and redo, ``\\`` shows the original (or, in Crop, the
result) while toggled.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable
from typing import Any, ClassVar

from PySide6.QtCore import QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QCursor,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from quince.core.operations import JobRequest
from quince.core.selection import Selection
from quince.ui.editors.canvas import ImageCanvas

SIDE_WIDTH = 270
WINDOW_FRACTION = 0.82
MAX_WINDOW = (1500, 1000)
SHARED_HINT = "Enter saves · Esc closes · Ctrl+Z undoes"


class History:
    """Undo / redo over immutable editor states.

    Changes pushed with the same ``merge`` key in quick succession (dragging a slider) become
    one step.
    """

    MERGE_S = 0.8

    def __init__(self, initial: Hashable, clock: Callable[[], float] = time.monotonic) -> None:
        self._states: list[Hashable] = [initial]
        self._index = 0
        self._clock = clock
        self._merge: tuple[str, float] | None = None

    @property
    def current(self) -> Hashable:
        return self._states[self._index]

    @property
    def initial(self) -> Hashable:
        return self._states[0]

    @property
    def dirty(self) -> bool:
        return self.current != self.initial

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._states) - 1

    def push(self, state: Hashable, merge: str | None = None) -> bool:
        """Record ``state``; False when it equals the current one."""
        if state == self.current:
            return False
        now = self._clock()
        mergeable = (
            merge is not None
            and self._merge is not None
            and self._merge[0] == merge
            and now - self._merge[1] < self.MERGE_S
            and self._index > 0
            and not self.can_redo
        )
        del self._states[self._index + 1 :]
        if mergeable:
            self._states[self._index] = state
            if state == self._states[self._index - 1]:  # merged back to where it started
                self._states.pop()
                self._index -= 1
        else:
            self._states.append(state)
            self._index += 1
        self._merge = (merge, now) if merge is not None else None
        return True

    def undo(self) -> Hashable | None:
        self._merge = None
        if not self.can_undo:
            return None
        self._index -= 1
        return self.current

    def redo(self) -> Hashable | None:
        self._merge = None
        if not self.can_redo:
            return None
        self._index += 1
        return self.current


def _typing(widget: QWidget | None) -> bool:
    return isinstance(widget, QLineEdit | QPlainTextEdit | QAbstractSpinBox)


class EditorWindow(QWidget):
    name: ClassVar[str] = ""
    title: ClassVar[str] = "Editor"
    compare_label: ClassVar[str] = "Original"  # "" hides the compare button
    save_label: ClassVar[str] = "Save"
    side_panel: ClassVar[bool] = False

    saved = Signal(object)  # JobRequest

    @classmethod
    def load(cls, selection: Selection) -> Any:
        """Read what the editor needs (off the GUI thread); raise ``JobError`` with a reason."""
        return None

    def __init__(self, selection: Selection, data: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.selection = selection
        self.data = data
        self.history: History | None = None
        self.confirm_discard: Callable[[], bool] = self._ask_discard
        self._done = False
        files = selection.files
        name = files[0].path.name if len(files) == 1 else f"{len(files)} images"
        self.setWindowTitle(f"{self.title} — {name}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.toolbar_area = QWidget()
        self.toolbar = QHBoxLayout(self.toolbar_area)
        self.toolbar.setContentsMargins(10, 8, 10, 8)
        self.toolbar.setSpacing(6)
        root.addWidget(self.toolbar_area)
        self.toolbar_line = _line()
        root.addWidget(self.toolbar_line)

        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(0)
        self.canvas = self.create_canvas()
        middle.addWidget(self.canvas, 1)
        self.side_area = QWidget()
        self.side_area.setFixedWidth(SIDE_WIDTH)
        self.side = QVBoxLayout(self.side_area)
        self.side.setContentsMargins(14, 12, 14, 12)
        self.side.setSpacing(8)
        self.side_area.setVisible(self.side_panel)
        middle.addWidget(self.side_area)
        root.addLayout(middle, 1)
        root.addWidget(_line())

        bottom = QHBoxLayout()
        bottom.setContentsMargins(12, 8, 12, 8)
        self.hint = QLabel(SHARED_HINT)
        self.hint.setEnabled(False)  # dimmed
        bottom.addWidget(self.hint, 1)
        self.info = QLabel()
        bottom.addWidget(self.info)
        bottom.addSpacing(12)
        self.compare_button = QPushButton(self.compare_label or "Original")
        self.compare_button.setCheckable(True)
        self.compare_button.setToolTip("Toggle with \\")
        self.compare_button.setVisible(bool(self.compare_label))
        self.compare_button.toggled.connect(self._on_compare)
        bottom.addWidget(self.compare_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.close)
        bottom.addWidget(self.cancel_button)
        self.save_button = QPushButton(self.save_label)
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save)
        bottom.addWidget(self.save_button)
        root.addLayout(bottom)

        for keys, slot in (
            (QKeySequence.StandardKey.Save, self.save),
            (QKeySequence.StandardKey.Undo, self.undo),
            (QKeySequence.StandardKey.Redo, self.redo),
            (QKeySequence("Ctrl+Y"), self.redo),
            (QKeySequence("\\"), self.compare_button.toggle),
        ):
            QShortcut(keys, self, slot, context=Qt.ShortcutContext.WindowShortcut)

    # --- for subclasses -----------------------------------------------------------------

    def create_canvas(self) -> QWidget:
        return ImageCanvas()

    def state(self) -> Hashable:
        raise NotImplementedError

    def set_state(self, state: Hashable) -> None:
        """Show ``state`` (undo / redo): update the controls and the preview."""
        raise NotImplementedError

    def build_job(self) -> JobRequest:
        raise NotImplementedError

    def can_save(self) -> bool:
        return self.history is not None and self.history.dirty

    def compare_image(self) -> tuple[QImage, QSizeF | tuple[float, float]] | None:
        return None

    def remember(self) -> None:
        """Keep settings for next time (called on Save)."""

    def start_history(self) -> None:
        self.history = History(self.state())
        self.update_buttons()

    def commit(self, merge: str | None = None) -> None:
        """Record the current state after a change made by the user."""
        if self.history is not None:
            self.history.push(self.state(), merge)
        self.update_buttons()

    def update_buttons(self) -> None:
        self.save_button.setEnabled(self.can_save())

    def set_hint(self, text: str) -> None:
        self.hint.setText(f"{text} · {SHARED_HINT}" if text else SHARED_HINT)

    def add_tool(self, widget: QWidget) -> QWidget:
        self.toolbar.addWidget(widget)
        return widget

    # --- actions ------------------------------------------------------------------------

    def save(self) -> None:
        if self._done or not self.can_save():
            return
        request = self.build_job()
        self.remember()
        self._done = True
        self.saved.emit(request)
        self.close()

    def undo(self) -> None:
        if self.history is not None and (state := self.history.undo()) is not None:
            self.set_state(state)
            self.update_buttons()

    def redo(self) -> None:
        if self.history is not None and (state := self.history.redo()) is not None:
            self.set_state(state)
            self.update_buttons()

    def _on_compare(self, on: bool) -> None:
        if isinstance(self.canvas, ImageCanvas):
            self.canvas.set_compare(self.compare_image() if on else None)

    def present(self) -> None:
        """Show the window centered on the screen under the pointer, sized to it."""
        empty = self.toolbar.count() == 0  # editors with only a side panel
        self.toolbar_area.setHidden(empty)
        self.toolbar_line.setHidden(empty)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            w = min(MAX_WINDOW[0], int(area.width() * WINDOW_FRACTION))
            h = min(MAX_WINDOW[1], int(area.height() * WINDOW_FRACTION))
            self.resize(w, h)
            self.move(area.x() + (area.width() - w) // 2, area.y() + (area.height() - h) // 2)
        self.show()
        self.raise_()
        self.activateWindow()
        self.canvas.setFocus()

    # --- keys and closing ---------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if _typing(self.focusWidget()):
                self.canvas.setFocus()  # Enter in a number field applies it, it does not save
            else:
                self.save()
            return
        if key == Qt.Key.Key_Escape:
            if _typing(self.focusWidget()):
                self.canvas.setFocus()
            else:
                self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        dirty = self.history is not None and self.history.dirty
        if not self._done and dirty and not self.confirm_discard():
            event.ignore()
            return
        self._done = True
        event.accept()

    def _ask_discard(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.title)
        box.setText("Discard your changes?")
        box.setInformativeText("Nothing has been saved yet.")
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is discard


# --- small shared widgets -----------------------------------------------------------------


def _line() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


def heading(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    return label


def tool_button(
    text: str, *, icon: str = "", tip: str = "", checkable: bool = False, raised: bool = False
) -> QToolButton:
    from quince.ui.host import icon_for

    button = QToolButton()
    button.setText(text)
    button.setToolTip(tip or text)
    button.setCheckable(checkable)
    themed = icon_for(icon) if icon else None
    if themed is not None and not themed.isNull():
        button.setIcon(themed)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    else:
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    button.setAutoRaise(not raised)
    if raised:
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return button


def separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


class Slider(QWidget):
    """A labeled slider with its value; double-click the label to reset it.

    ``changed(value)`` on every move (commit with a ``merge`` key so a drag is one undo step).
    """

    changed = Signal(int)

    def __init__(
        self,
        label: str,
        minimum: int,
        maximum: int,
        value: int,
        *,
        default: int | None = None,
        show: Callable[[int], str] = str,
    ) -> None:
        super().__init__()
        self._default = value if default is None else default
        self._show = show
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        top = QHBoxLayout()
        self.label = QLabel(label)
        self.label.setToolTip("Double-click to reset")
        self.label.mouseDoubleClickEvent = lambda _e: self.reset()  # type: ignore[method-assign]
        top.addWidget(self.label)
        top.addStretch(1)
        self.value_label = QLabel()
        top.addWidget(self.value_label)
        layout.addLayout(top)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setValue(value)
        self.slider.valueChanged.connect(self._on_change)
        layout.addWidget(self.slider)
        self._update_label()

    def value(self) -> int:
        return self.slider.value()

    def set_value(self, value: int) -> None:
        """Set without emitting ``changed`` (undo / redo)."""
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self._update_label()

    def reset(self) -> None:
        self.slider.setValue(self._default)

    def _on_change(self, value: int) -> None:
        self._update_label()
        self.changed.emit(value)

    def _update_label(self) -> None:
        self.value_label.setText(self._show(self.slider.value()))


RGBA = tuple[int, int, int, int]


class Swatches(QWidget):
    """A row of color buttons, optionally with "Custom…" (a color dialog) and transparency.

    ``picked(rgba)`` when the user chooses a color.
    """

    picked = Signal(object)
    SIZE = 22

    def __init__(
        self,
        colors: list[tuple[str, RGBA]],
        current: RGBA,
        *,
        custom: bool = True,
        alpha: bool = False,
    ) -> None:
        super().__init__()
        self._alpha = alpha
        self._current = tuple(current)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[RGBA, QToolButton] = {}
        for name, color in colors:
            button = self._swatch(name, tuple(color))
            layout.addWidget(button)
        self.custom_button = None
        if custom:
            self.custom_button = QToolButton()
            self.custom_button.setText("…")
            self.custom_button.setToolTip("Other color…")
            self.custom_button.setCheckable(True)
            self.custom_button.setFixedSize(self.SIZE + 4, self.SIZE + 4)
            self.custom_button.clicked.connect(self._choose)
            self.group.addButton(self.custom_button)
            layout.addWidget(self.custom_button)
        layout.addStretch(1)
        self.set_color(current)

    def _swatch(self, name: str, color: RGBA) -> QToolButton:
        button = QToolButton()
        button.setToolTip(name)
        button.setCheckable(True)
        button.setFixedSize(self.SIZE + 4, self.SIZE + 4)
        button.setStyleSheet(_swatch_style(color))
        button.clicked.connect(lambda _c=False, c=color: self._pick(c))
        self.group.addButton(button)
        self.buttons[color] = button
        return button

    def color(self) -> RGBA:
        return self._current  # type: ignore[return-value]

    def set_color(self, color: RGBA) -> None:
        self._current = tuple(color)
        button = self.buttons.get(self._current)  # type: ignore[arg-type]
        if button is not None:
            button.setChecked(True)
        elif self.custom_button is not None:
            self.custom_button.setChecked(True)
            self.custom_button.setStyleSheet(_swatch_style(self._current))

    def _pick(self, color: RGBA) -> None:
        self.set_color(color)
        self.picked.emit(self._current)

    def _choose(self) -> None:
        options = QColorDialog.ColorDialogOption(0)
        if self._alpha:
            options |= QColorDialog.ColorDialogOption.ShowAlphaChannel
        chosen = QColorDialog.getColor(QColor(*self._current), self, "Choose a Color", options)
        if chosen.isValid():
            self._pick((chosen.red(), chosen.green(), chosen.blue(), chosen.alpha()))
        else:
            self.set_color(self._current)  # cancelled: check the previous swatch again


def _swatch_style(color: tuple[int, ...]) -> str:
    r, g, b, *rest = color
    a = rest[0] if rest else 255
    if a == 0:
        fill = (
            "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ffffff, stop:0.45 #ffffff, "
            "stop:0.5 #d33, stop:0.55 #ffffff, stop:1 #ffffff)"
        )
    else:
        fill = f"rgba({r}, {g}, {b}, {a})"
    return (
        f"QToolButton {{ background: {fill}; border: 1px solid rgba(0,0,0,90); "
        "border-radius: 4px; margin: 2px; }"
        "QToolButton:checked { border: 2px solid palette(highlight); margin: 0px; }"
    )


def debounced(parent: QWidget, slot: Callable[[], None], ms: int = 15) -> QTimer:
    """A single-shot timer that calls ``slot``; ``start()`` it to schedule a refresh."""
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.setInterval(ms)
    timer.timeout.connect(slot)
    return timer


def expanding_spacer() -> QWidget:
    spacer = QWidget()
    spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return spacer
