from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from quince.core import config
from quince.core.config import CONVERTED, Config, OutputLocation
from quince.core.deps import Availability
from quince.core.history import History
from quince.core.registry import discover
from quince.ui.library.model import LibraryModel
from quince.ui.settings.window import SettingsWindow
from quince.ui.shell import Shell
from quince.ui.theme.colors import apply_theme
from quince.ui.wheel.window import WheelWindow


@pytest.fixture(scope="module")
def registry():
    return discover()


class Harness:
    def __init__(self, qtbot, registry, tmp_path: Path, cfg: Config | None = None):
        self.path = tmp_path / "config.toml"
        if cfg is not None:
            config.save(cfg, self.path)
        self.autostart = False
        self.folder: Path | None = tmp_path / "out"
        self.changes: list[Config] = []
        self.window = SettingsWindow(
            registry,
            load=lambda: config.load(self.path),
            save=lambda c: config.save(c, self.path),
            probe=lambda: Availability([]),
            autostart_enabled=lambda: self.autostart,
            set_autostart=self._set_autostart,
            pick_folder=lambda _parent, _start: self.folder,
        )
        self.window.changed.connect(self.changes.append)
        qtbot.addWidget(self.window)

    def _set_autostart(self, on: bool) -> None:
        self.autostart = on

    def saved(self) -> Config:
        return config.load(self.path)


@pytest.fixture
def harness(qtbot, registry, tmp_path):
    return Harness(qtbot, registry, tmp_path)


def test_trigger_keys_are_saved_and_conflicts_are_not(harness):
    w = harness.window
    w.key_boxes[("convert", "Shift_R")].setChecked(False)
    assert harness.saved().triggers.keys["convert"] == ("Shift_L",)
    assert harness.changes[-1] == harness.saved()

    w.key_boxes[("tools", "Shift_L")].setChecked(True)  # Shift_L is Convert's already
    assert "Left Shift is chosen for both Convert and Tools" in w.trigger_problems.text()
    assert harness.saved().triggers.keys["tools"] == ("Control_L", "Control_R")
    count = len(harness.changes)

    w.key_boxes[("convert", "Shift_L")].setChecked(False)  # fixed: Tools gets it
    keys = harness.saved().triggers.keys
    assert keys["tools"] == ("Shift_L", "Control_L", "Control_R")
    assert keys["convert"] == ()
    assert len(harness.changes) == count + 1
    assert "Convert has no key" in w.trigger_problems.text()

    w.tap_spin.setValue(250)
    assert harness.saved().triggers.max_tap_ms == 250


def test_super_is_not_offered(harness):
    assert not [k for (_w, k) in harness.window.key_boxes if k.startswith("Super")]


def test_wheel_items_can_be_turned_off_and_moved(harness):
    w = harness.window
    items = w.wheel_lists["convert"]
    jpg = next(i for i in range(items.count()) if items.item(i).data(Qt.ItemDataRole.UserRole)
               == "convert.image.jpg")  # fmt: skip
    items.item(jpg).setCheckState(Qt.CheckState.Unchecked)
    assert harness.saved().wheel("convert").disabled == {"convert.image.jpg"}

    items.setCurrentRow(jpg)
    w._move("convert", -1 if jpg else 1)
    order = harness.saved().wheel("convert").order
    assert order and ("convert.image.jpg" in order)

    w._reset_wheel("convert")
    assert harness.saved().wheel("convert") == config.WheelPrefs()
    state = items.item(jpg).checkState()
    assert state == Qt.CheckState.Checked


def test_unavailable_items_are_listed_with_the_reason(harness):
    text = harness.window.unavailable_labels["tools"].text()
    assert "Unavailable" in text and "needs " in text


def test_output_folder_and_names(harness, tmp_path):
    w = harness.window
    w.folder_radio.setChecked(True)  # no folder yet: the chooser opens
    out = harness.saved().output
    assert out.location is OutputLocation.FOLDER and out.folder == tmp_path / "out"
    w.source_radio.setChecked(True)
    assert harness.saved().output.location is OutputLocation.SOURCE
    assert harness.saved().output.folder == tmp_path / "out"  # remembered for next time

    edit = w.suffix_edits["compressed"]
    edit.setText(" small/er ")
    edit.editingFinished.emit()
    assert harness.saved().output.suffix("compressed") == "small-er"
    assert w.suffix_examples["compressed"].text() == "photo-small-er.jpg"
    w.suffix_edits[CONVERTED].setText("copy")
    w.suffix_edits[CONVERTED].editingFinished.emit()
    assert harness.saved().output.suffix(CONVERTED) == "copy"

    w._reset_suffixes()
    assert dict(harness.saved().output.suffixes) == {}
    assert edit.text() == ""


def test_folder_choice_cancelled_stays_next_to_the_source(harness):
    harness.folder = None
    harness.window.folder_radio.setChecked(True)
    assert harness.window.source_radio.isChecked()
    assert harness.saved().output.location is OutputLocation.SOURCE


def test_appearance_and_start_at_login(harness):
    w = harness.window
    w.theme_combo.setCurrentIndex(w.theme_combo.findData("dark"))
    w.size_combo.setCurrentIndex(w.size_combo.findData("large"))
    w.animations_box.setChecked(False)
    assert harness.saved().appearance == config.Appearance("dark", "large", False)
    w.autostart_box.setChecked(True)
    assert harness.autostart is True
    w.autostart_box.setChecked(False)
    assert harness.autostart is False


def test_present_reads_the_file_again(qtbot, registry, tmp_path):
    h = Harness(qtbot, registry, tmp_path)
    changed = config.Config(appearance=config.Appearance("light", "small", True))
    config.save(changed, h.path)
    h.window.present()
    assert h.window.theme_combo.currentData() == "light"
    assert h.window.size_combo.currentData() == "small"
    assert h.changes == []  # filling the controls saves nothing
    h.window.close()


def test_a_save_error_is_shown(qtbot, registry, tmp_path):
    h = Harness(qtbot, registry, tmp_path)

    def fail(_cfg):
        raise PermissionError("read-only")

    h.window._save = fail
    h.window.animations_box.setChecked(False)
    assert "Could not save the settings" in h.window.status.text()
    assert h.changes == []


def test_theme_sets_the_color_scheme_of_the_app():
    class Hints:
        scheme = None

        def setColorScheme(self, scheme):
            self.scheme = scheme

    hints = Hints()
    for theme, scheme in (
        ("dark", Qt.ColorScheme.Dark),
        ("light", Qt.ColorScheme.Light),
        ("system", Qt.ColorScheme.Unknown),
    ):
        apply_theme(theme, hints)
        assert hints.scheme == scheme
    with pytest.raises(ValueError):
        apply_theme("blue", hints)


def _shell(qtbot, tmp_path):
    wheel = WheelWindow()
    qtbot.addWidget(wheel)
    shell = Shell(wheel, discover(), LibraryModel(History(tmp_path / "h.sqlite3")),
                  run_operation=lambda op, sel: None, drag_busy=lambda: False,
                  notify=lambda _t: None, quit_app=lambda: None, load_config=Config,
                  tray=False)  # fmt: skip
    return shell, wheel


def test_ctrl_comma_opens_settings_from_any_window(qtbot, tmp_path):
    shell, wheel = _shell(qtbot, tmp_path)
    try:
        other = QWidget()
        qtbot.addWidget(other)
        other.show()
        qtbot.keyClick(other, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
        assert shell._settings is not None and shell.settings.isVisible()
        shell.settings.close()

        qtbot.keyClick(wheel, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
        assert not shell.settings.isVisible()  # the wheel keeps its keys
        qtbot.keyClick(other, Qt.Key.Key_Comma)  # no Ctrl
        assert not shell.settings.isVisible()
        assert shell.handlers()["settings"]("") == "ok"
        assert shell.settings.isVisible()
    finally:
        QApplication.instance().removeEventFilter(shell)
        if shell._settings is not None:
            shell._settings.close()
            shell._settings.deleteLater()


def test_saved_settings_reach_the_service_and_home(qtbot, tmp_path):
    shell, _wheel = _shell(qtbot, tmp_path)
    try:
        got = []
        shell.config_changed.connect(got.append)
        window = shell.window
        qtbot.addWidget(window)
        keys = {"convert": ("Control_R",), "tools": ("Shift_L",), "actions": ("Alt_L",)}
        cfg = Config(triggers=config.Triggers(keys))
        shell.settings.changed.emit(cfg)
        assert got == [cfg]
        assert "tap Right Ctrl / Left Shift / Left Alt" in window.home.hint.text()
        assert window.home.taps.taps.trigger_keys == {"Control_R", "Shift_L", "Alt_L"}
    finally:
        QApplication.instance().removeEventFilter(shell)
