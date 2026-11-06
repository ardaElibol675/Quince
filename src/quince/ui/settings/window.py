"""The Settings window: General (login, appearance), Triggers, Wheels and Output.

Every change is saved to ``config.toml`` at once and ``changed`` is sent with the new config,
so the service applies it without a restart (GNOME style: no Apply button). Trigger keys
that clash (a key for two wheels) are shown in red and not saved until they are fixed.
Start at login is the autostart file itself, not part of the config.

Keys: Ctrl+1 … Ctrl+4 switch pages; Esc / Ctrl+W close the window.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from quince.core import config
from quince.core.config import (
    MAX_SUFFIX_CHARS,
    MAX_TAP_MS_RANGE,
    WHEEL_SIZES,
    WHEELS,
    Config,
    OutputLocation,
    clean_suffix,
)
from quince.core.deps import Availability
from quince.core.registry import Registry
from quince.integrations import autostart
from quince.ui.settings import model
from quince.ui.theme.icons import themed

log = logging.getLogger(__name__)

WINDOW_SIZE = (820, 600)
PAGES = ("General", "Triggers", "Wheels", "Output")
THEME_LABELS = {"system": "Follow the system", "light": "Light", "dark": "Dark"}
SIZE_LABELS = {"small": "Small", "medium": "Medium", "large": "Large"}
ERROR_COLOR = "#c0392b"
ID_ROLE = Qt.ItemDataRole.UserRole


def choose_folder(parent: QWidget, start: Path | None) -> Path | None:
    from quince.core import recent
    from quince.ui.host import _exec_dialog, folder_dialog

    dialog = folder_dialog(
        "Output Folder", accept_label="Choose", start=start, recent=recent.destinations()
    )
    try:
        return _exec_dialog(dialog)
    finally:
        dialog.deleteLater()


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setBold(True)
    font.setPointSizeF(font.pointSizeF() * 1.15)
    label.setFont(font)
    return label


def _dim(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setEnabled(False)  # the palette's disabled text: dim in light and dark
    return label


def _page(*widgets: QWidget) -> tuple[QScrollArea, QVBoxLayout]:
    inner = QWidget()
    layout = QVBoxLayout(inner)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)
    for widget in widgets:
        layout.addWidget(widget)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(inner)
    return scroll, layout


class SettingsWindow(QWidget):
    changed = Signal(object)  # Config, after it was saved

    def __init__(
        self,
        registry: Registry,
        *,
        load: Callable[[], Config] = config.load,
        save: Callable[[Config], object] = config.save,
        probe: Callable[[], Availability] = Availability.probe,
        autostart_enabled: Callable[[], bool] = autostart.is_enabled,
        set_autostart: Callable[[bool], object] = autostart.set_enabled,
        pick_folder: Callable[[QWidget, Path | None], Path | None] = choose_folder,
    ) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("Quince Settings")
        self.resize(*WINDOW_SIZE)
        self.registry = registry
        self._load = load
        self._save = save
        self._probe = probe
        self._autostart_enabled = autostart_enabled
        self._set_autostart = set_autostart
        self._pick_folder = pick_folder
        self.config = load()
        self._filling = False  # controls are being set from the config: no saving

        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(160)
        self.sidebar.setFrameShape(QFrame.Shape.NoFrame)
        self.sidebar.setIconSize(QSize(20, 20))
        self.sidebar.setStyleSheet(
            "QListWidget { padding-top: 8px; } QListWidget::item { padding: 6px 10px; }"
        )
        self.stack = QStackedWidget()
        for name, icon, page in (
            ("General", "preferences-system", self._general_page()),
            ("Triggers", "input-keyboard", self._triggers_page()),
            ("Wheels", "quince-wheel", self._wheels_page()),
            ("Output", "folder", self._output_page()),
        ):
            QListWidgetItem(themed(icon), name, self.sidebar)
            self.stack.addWidget(page)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {ERROR_COLOR};")
        self.status.hide()
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.stack, 1)
        right.addWidget(self.status)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.sidebar)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(line)
        root.addLayout(right, 1)

        for index in range(len(PAGES)):
            QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self, lambda i=index: self.show_page(i))
        QShortcut(QKeySequence.StandardKey.Close, self, self.close)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)
        self._fill()

    # --- public -------------------------------------------------------------------------

    def present(self) -> None:
        """Show the window with the current settings (read again: the file may have changed)."""
        if not self.isVisible():
            self.config = self._load()
            self._fill()
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def show_page(self, index: int) -> None:
        self.sidebar.setCurrentRow(index)

    # --- pages ----------------------------------------------------------------------------

    def _general_page(self) -> QWidget:
        self.autostart_box = QCheckBox("Start Quince when I log in")
        self.autostart_box.toggled.connect(self._on_autostart)
        login_hint = _dim(
            "The background service watches for drags, so the wheels are ready without opening "
            "Quince first."
        )

        self.theme_combo = QComboBox()
        for key in config.THEMES:
            self.theme_combo.addItem(THEME_LABELS[key], key)
        self.size_combo = QComboBox()
        for key in WHEEL_SIZES:
            self.size_combo.addItem(SIZE_LABELS.get(key, key.capitalize()), key)
        self.animations_box = QCheckBox("Animate the wheel")
        appearance = QFormLayout()
        appearance.addRow("Theme", self.theme_combo)
        appearance.addRow("Wheel size", self.size_combo)
        appearance.addRow("", self.animations_box)
        for combo in (self.theme_combo, self.size_combo):
            combo.currentIndexChanged.connect(self._on_appearance)
        self.animations_box.toggled.connect(self._on_appearance)
        box = QWidget()
        box.setLayout(appearance)

        page, layout = _page(
            _heading("Startup"),
            self.autostart_box,
            login_hint,
            _heading("Appearance"),
            box,
            _dim("The theme applies to every Quince window."),
        )
        layout.addStretch(1)
        return page

    def _triggers_page(self) -> QWidget:
        intro = _dim(
            "While dragging files, tap a key (press and release it on its own) to open that "
            "wheel. Taps count only during a drag, so Shift-click and Ctrl-click keep working."
        )
        page, layout = _page(_heading("Trigger keys"), intro)
        self.key_boxes: dict[tuple[str, str], QCheckBox] = {}
        self._key_grids: dict[str, QGridLayout] = {}
        for wheel in WHEELS:
            group = QGroupBox(model.WHEEL_NAMES[wheel])
            grid = QGridLayout(group)
            self._key_grids[wheel] = grid
            layout.addWidget(group)
        self.trigger_problems = QLabel()
        self.trigger_problems.setWordWrap(True)
        self.trigger_problems.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.trigger_problems)

        self.tap_spin = QSpinBox()
        self.tap_spin.setRange(*MAX_TAP_MS_RANGE)
        self.tap_spin.setSingleStep(50)
        self.tap_spin.setSuffix(" ms")
        self.tap_spin.setKeyboardTracking(False)  # typed numbers count when editing ends
        self.tap_spin.valueChanged.connect(lambda _v: self._on_triggers())
        row = QHBoxLayout()
        row.addWidget(QLabel("A tap is shorter than"))
        row.addWidget(self.tap_spin)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(_dim("Holding a key longer than this is not a tap."))
        layout.addStretch(1)
        return page

    def _build_key_boxes(self) -> None:
        """One check box per offered key and wheel (config-only keys are offered too)."""
        keys = model.offered_keys(self.config.triggers.keys)
        if [k for (_w, k) in self.key_boxes if _w == WHEELS[0]] == keys:
            return
        for box in self.key_boxes.values():
            box.deleteLater()
        self.key_boxes.clear()
        for wheel in WHEELS:
            grid = self._key_grids[wheel]
            for i, key in enumerate(keys):
                box = QCheckBox(model.key_label(key))
                box.toggled.connect(lambda _on: self._on_triggers())
                grid.addWidget(box, i // 4, i % 4)
                self.key_boxes[(wheel, key)] = box

    def _wheels_page(self) -> QWidget:
        intro = _dim(
            "Turn items off to hide them from a wheel; drag them (or use the buttons) to change "
            "the order. A wheel shows only the items that fit the dragged files."
        )
        self.wheel_tabs = QTabWidget()
        self.wheel_lists: dict[str, QListWidget] = {}
        self.unavailable_labels: dict[str, QLabel] = {}
        for wheel in WHEELS:
            items = QListWidget()
            items.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
            items.setDefaultDropAction(Qt.DropAction.MoveAction)
            items.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            items.setIconSize(QSize(20, 20))
            items.itemChanged.connect(lambda _item, w=wheel: self._on_wheel(w))
            items.model().rowsMoved.connect(lambda *_a, w=wheel: self._on_wheel(w))
            self.wheel_lists[wheel] = items

            up = QPushButton(themed("go-up"), "Move Up")
            down = QPushButton(themed("go-down"), "Move Down")
            reset = QPushButton("Restore Default")
            up.clicked.connect(lambda _c=False, w=wheel: self._move(w, -1))
            down.clicked.connect(lambda _c=False, w=wheel: self._move(w, 1))
            reset.clicked.connect(lambda _c=False, w=wheel: self._reset_wheel(w))
            buttons = QHBoxLayout()
            for button in (up, down):
                buttons.addWidget(button)
            buttons.addStretch(1)
            buttons.addWidget(reset)

            hidden = QLabel()
            hidden.setWordWrap(True)
            hidden.setTextFormat(Qt.TextFormat.RichText)
            hidden.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.unavailable_labels[wheel] = hidden

            tab = QWidget()
            column = QVBoxLayout(tab)
            column.addWidget(items, 1)
            column.addLayout(buttons)
            column.addWidget(hidden)
            self.wheel_tabs.addTab(tab, model.WHEEL_NAMES[wheel])
        page, layout = _page(_heading("Wheels"), intro, self.wheel_tabs)
        layout.setStretchFactor(self.wheel_tabs, 1)
        return page

    def _output_page(self) -> QWidget:
        self.source_radio = QRadioButton("Next to the original file")
        self.folder_radio = QRadioButton("In one folder:")
        self._location_group = QButtonGroup(self)
        self._location_group.addButton(self.source_radio)
        self._location_group.addButton(self.folder_radio)
        self.folder_label = QLabel()
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.folder_button = QPushButton("Choose…")
        self.folder_button.clicked.connect(self._choose_folder)
        self.source_radio.toggled.connect(lambda on: on and self._on_location())
        self.folder_radio.toggled.connect(lambda on: on and self._on_location())
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_radio)
        folder_row.addWidget(self.folder_label, 1)
        folder_row.addWidget(self.folder_button)
        location = QWidget()
        column = QVBoxLayout(location)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.source_radio)
        column.addLayout(folder_row)

        names = QWidget()
        form = QGridLayout(names)
        form.setContentsMargins(0, 0, 0, 0)
        form.setColumnStretch(1, 1)
        self.suffix_rows = model.suffix_rows(self.registry)
        self.suffix_edits: dict[str, QLineEdit] = {}
        self.suffix_examples: dict[str, QLabel] = {}
        for i, row in enumerate(self.suffix_rows):
            label = QLabel(row.used_by)
            label.setWordWrap(True)
            edit = QLineEdit()
            edit.setMaxLength(MAX_SUFFIX_CHARS)
            edit.setPlaceholderText(row.default or "no suffix")
            edit.textChanged.connect(lambda _t, r=row: self._show_example(r))
            edit.editingFinished.connect(self._on_suffixes)
            example = _dim()
            example.setWordWrap(False)
            form.addWidget(label, i, 0)
            form.addWidget(edit, i, 1)
            form.addWidget(example, i, 2)
            self.suffix_edits[row.key] = edit
            self.suffix_examples[row.key] = example
        reset = QPushButton("Restore Default Names")
        reset.clicked.connect(self._reset_suffixes)
        reset_row = QHBoxLayout()
        reset_row.addStretch(1)
        reset_row.addWidget(reset)

        page, layout = _page(
            _heading("Where new files go"),
            location,
            _dim("Originals are never changed; a taken name becomes “name (1)”."),
            _heading("Names of new files"),
            _dim("The word added to the name of what each tool makes. Conversions add none."),
            names,
        )
        layout.addLayout(reset_row)
        layout.addStretch(1)
        return page

    # --- filling the controls ---------------------------------------------------------------

    def _fill(self) -> None:
        self._filling = True
        try:
            cfg = self.config
            self.autostart_box.setChecked(self._autostart_enabled())
            look = cfg.appearance
            self.theme_combo.setCurrentIndex(self.theme_combo.findData(look.theme))
            self.size_combo.setCurrentIndex(self.size_combo.findData(look.wheel_size))
            self.animations_box.setChecked(look.animations)

            self._build_key_boxes()
            for (wheel, key), box in self.key_boxes.items():
                box.setChecked(key in cfg.triggers.keys.get(wheel, ()))
            self.tap_spin.setValue(cfg.triggers.max_tap_ms)
            self._show_trigger_check(model.check_triggers(cfg.triggers.keys))

            available = self._probe()
            for wheel in WHEELS:
                self._fill_wheel(wheel)
                self._fill_unavailable(wheel, available)

            out = cfg.output
            folder = out.location is OutputLocation.FOLDER
            (self.folder_radio if folder else self.source_radio).setChecked(True)
            self._show_folder()
            for row in self.suffix_rows:
                word = out.suffixes.get(row.key, row.default)
                self.suffix_edits[row.key].setText("" if word == row.default else word)
                self._show_example(row)
        finally:
            self._filling = False

    def _fill_wheel(self, wheel: str) -> None:
        items = self.wheel_lists[wheel]
        items.blockSignals(True)
        try:
            items.clear()
            for row in model.wheel_rows(self.registry, wheel, self.config.wheel(wheel)):
                item = QListWidgetItem(themed(row.icon), row.text)
                item.setData(ID_ROLE, row.id)
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsDragEnabled
                )
                item.setCheckState(
                    Qt.CheckState.Checked if row.enabled else Qt.CheckState.Unchecked
                )
                items.addItem(item)
        finally:
            items.blockSignals(False)

    def _fill_unavailable(self, wheel: str, available: Availability) -> None:
        groups = model.unavailable_by_reason(self.registry, available, wheel)
        label = self.unavailable_labels[wheel]
        if not groups:
            label.setText("")
            label.hide()
            return
        parts = ["<b>Unavailable</b> (missing programs):"]
        for reason, what in groups:
            parts.append(f"<br>• {_escape(', '.join(what))} — <i>{_escape(reason)}</i>")
        label.setText("".join(parts))
        label.show()

    def _show_trigger_check(self, check: model.TriggerCheck) -> None:
        lines = [f'<span style="color:{ERROR_COLOR}">{_escape(e)}</span>' for e in check.errors]
        lines += [_escape(w) for w in check.warnings]
        self.trigger_problems.setText("<br>".join(lines))
        self.trigger_problems.setVisible(bool(lines))

    def _show_folder(self) -> None:
        folder = self.config.output.folder
        self.folder_label.setText(str(folder) if folder else "(no folder chosen)")

    def _show_example(self, row: model.SuffixRow) -> None:
        word = clean_suffix(self.suffix_edits[row.key].text()) or row.default
        self.suffix_examples[row.key].setText(model.example_name(word))

    # --- changes ----------------------------------------------------------------------------

    def _commit(self, new: Config) -> bool:
        """Save ``new`` and tell the service. False when it could not be saved."""
        if new == self.config:
            return True
        try:
            self._save(new)
        except OSError as exc:
            log.warning("cannot save the settings: %s", exc)
            self._show_status(f"Could not save the settings: {exc}")
            return False
        self.config = new
        self._show_status("")
        log.info("settings saved")
        self.changed.emit(new)
        return True

    def _show_status(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _on_autostart(self, on: bool) -> None:
        if self._filling:
            return
        try:
            self._set_autostart(on)
        except OSError as exc:
            log.warning("cannot change start at login: %s", exc)
            self._show_status(f"Could not change “Start at login”: {exc}")
            self._filling = True
            self.autostart_box.setChecked(not on)
            self._filling = False

    def _on_appearance(self, *_args) -> None:
        if self._filling:
            return
        look = config.Appearance(
            self.theme_combo.currentData(),
            self.size_combo.currentData(),
            self.animations_box.isChecked(),
        )
        self._commit(replace(self.config, appearance=look))

    def _current_triggers(self) -> dict[str, tuple[str, ...]]:
        keys: dict[str, list[str]] = {wheel: [] for wheel in WHEELS}
        for (wheel, key), box in self.key_boxes.items():
            if box.isChecked():
                keys[wheel].append(key)
        return {wheel: tuple(k) for wheel, k in keys.items()}

    def _on_triggers(self) -> None:
        if self._filling:
            return
        keys = self._current_triggers()
        check = model.check_triggers(keys)
        self._show_trigger_check(check)
        if check.errors:
            return
        triggers = config.Triggers(MappingProxyType(keys), self.tap_spin.value())
        self._commit(replace(self.config, triggers=triggers))

    def _rows(self, wheel: str) -> list[tuple[str, bool]]:
        items = self.wheel_lists[wheel]
        return [
            (
                items.item(i).data(ID_ROLE),
                items.item(i).checkState() == Qt.CheckState.Checked,
            )
            for i in range(items.count())
        ]

    def _on_wheel(self, wheel: str) -> None:
        if self._filling:
            return
        prefs = model.prefs_from_rows(self.registry, wheel, self._rows(wheel))
        wheels = dict(self.config.wheels)
        wheels[wheel] = prefs
        self._commit(replace(self.config, wheels=MappingProxyType(wheels)))

    def _move(self, wheel: str, step: int) -> None:
        items = self.wheel_lists[wheel]
        row = items.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < items.count():
            return
        items.blockSignals(True)
        item = items.takeItem(row)
        items.insertItem(target, item)
        items.blockSignals(False)
        items.setCurrentRow(target)
        self._on_wheel(wheel)

    def _reset_wheel(self, wheel: str) -> None:
        wheels = dict(self.config.wheels)
        wheels[wheel] = config.WheelPrefs()
        if self._commit(replace(self.config, wheels=MappingProxyType(wheels))):
            self._fill_wheel(wheel)

    def _on_location(self) -> None:
        if self._filling:
            return
        out = self.config.output
        if self.folder_radio.isChecked():
            folder = out.folder
            if folder is None:
                folder = self._pick_folder(self, None)
                if folder is None:  # cancelled: stay next to the source
                    self._filling = True
                    self.source_radio.setChecked(True)
                    self._filling = False
                    return
            new = replace(out, location=OutputLocation.FOLDER, folder=Path(folder))
        else:
            new = replace(out, location=OutputLocation.SOURCE)
        self._commit(replace(self.config, output=new))
        self._show_folder()

    def _choose_folder(self) -> None:
        out = self.config.output
        folder = self._pick_folder(self, out.folder)
        if folder is None:
            return
        new = replace(out, location=OutputLocation.FOLDER, folder=Path(folder))
        if self._commit(replace(self.config, output=new)):
            self._filling = True
            self.folder_radio.setChecked(True)
            self._filling = False
            self._show_folder()

    def _on_suffixes(self) -> None:
        if self._filling:
            return
        suffixes = dict(self.config.output.suffixes)
        for row in self.suffix_rows:
            edit = self.suffix_edits[row.key]
            word = clean_suffix(edit.text())
            if word != edit.text() and not edit.hasFocus():
                edit.setText(word if word != row.default else "")
            if not word or word == row.default:
                suffixes.pop(row.key, None)
            else:
                suffixes[row.key] = word
        new = replace(self.config.output, suffixes=MappingProxyType(dict(sorted(suffixes.items()))))
        self._commit(replace(self.config, output=new))

    def _reset_suffixes(self) -> None:
        keys = {row.key for row in self.suffix_rows}
        kept = {k: v for k, v in self.config.output.suffixes.items() if k not in keys}
        new = replace(self.config.output, suffixes=MappingProxyType(kept))
        if self._commit(replace(self.config, output=new)):
            self._filling = True
            for row in self.suffix_rows:
                self.suffix_edits[row.key].setText("")
                self._show_example(row)
            self._filling = False


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
