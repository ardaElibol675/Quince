"""End-to-end drag + tap + drop in a nested X server (Xvfb, or Xephyr when opted in).

A GTK3 drag source stands in for Nautilus 42 (same drag-source code), so GTK's behavior
during a drag is covered too: Enter drops at the pointer, right click drops, Esc cancels.

Input is injected with XTest, which goes through the real input pipeline and produces
raw events (``xdotool mousemove`` warps the pointer and would not).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

HERE = Path(__file__).resolve().parent
SYSTEM_PYTHON = "/usr/bin/python3"
DRAG_START = (100, 100)
OVERLAY_AT = (150, 150)  # where the pointer is when the trigger is tapped


def _server_command(number: int) -> list[str] | None:
    if shutil.which("Xvfb"):
        return ["Xvfb", f":{number}", "-screen", "0", "800x600x24", "-nolisten", "tcp"]
    if shutil.which("Xephyr") and os.environ.get("QUINCE_NESTED_X") == "1":
        return ["Xephyr", f":{number}", "-screen", "800x600", "-ac", "-br", "-noreset"]
    return None


def _has_system_gtk3() -> bool:
    if not Path(SYSTEM_PYTHON).exists():
        return False
    probe = "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return (
        subprocess.run([SYSTEM_PYTHON, "-c", probe], env=env, capture_output=True).returncode == 0
    )


def _free_display() -> int:
    for number in range(77, 120):
        if not Path(f"/tmp/.X11-unix/X{number}").exists():
            return number
    raise RuntimeError("no free X display number")


def _wait_for(predicate, timeout=10.0, step=0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return False


class Session:
    """A nested X server running ``quince spike-drag`` and, optionally, a GTK drag source."""

    def __init__(self, display_name: str, disp, tmp_path: Path) -> None:
        self.disp = disp
        self.tmp_path = tmp_path
        self.env = {
            **os.environ,
            "DISPLAY": display_name,
            "QT_QPA_PLATFORM": "xcb",
            "GDK_BACKEND": "x11",
            "NO_AT_BRIDGE": "1",
        }
        self.env.pop("PYTHONPATH", None)
        self.spike_log = tmp_path / "spike.log"
        self._procs: list[subprocess.Popen] = []
        self._source_lines: list[str] = []

    # --- processes ----------------------------------------------------------

    def start_spike(self) -> None:
        with self.spike_log.open("w") as log_file:
            proc = subprocess.Popen(
                [sys.executable, "-m", "quince", "spike-drag", "-v"],
                env=self.env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        self._procs.append(proc)
        assert _wait_for(lambda: "spike-drag running" in self.log), self.log

    def start_source(self, *paths: Path) -> None:
        proc = subprocess.Popen(
            [SYSTEM_PYTHON, str(HERE / "gtk_drag_source.py"), *map(str, paths)],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._procs.append(proc)
        reader = threading.Thread(target=self._read_source, args=(proc,), daemon=True)
        reader.start()
        assert _wait_for(lambda: "READY" in self._source_lines), "GTK source did not start"
        time.sleep(0.3)

    def _read_source(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            self._source_lines.append(line.strip())

    def stop(self) -> None:
        for proc in self._procs:
            proc.terminate()
        for proc in self._procs:
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()

    @property
    def log(self) -> str:
        return self.spike_log.read_text()

    @property
    def source(self) -> list[str]:
        return list(self._source_lines)

    def wait_log(self, text: str, timeout: float = 5.0) -> None:
        assert _wait_for(lambda: text in self.log, timeout), f"{text!r} not in log:\n{self.log}"

    def wait_source(self, text: str, timeout: float = 5.0) -> None:
        assert _wait_for(lambda: any(text in s for s in self._source_lines), timeout), (
            f"{text!r} not in source output {self.source}"
        )

    # --- input (XTest) --------------------------------------------------------

    def _fake(self, *args, **kwargs) -> None:
        from Xlib.ext import xtest

        xtest.fake_input(self.disp, *args, **kwargs)
        self.disp.sync()

    def move(self, x: int, y: int, pause: float = 0.06) -> None:
        from Xlib import X

        self._fake(X.MotionNotify, x=x, y=y)
        time.sleep(pause)

    def button(self, number: int, down: bool) -> None:
        from Xlib import X

        self._fake(X.ButtonPress if down else X.ButtonRelease, number)
        time.sleep(0.2)

    def key(self, keysym: str, down: bool) -> None:
        from Xlib import XK, X

        code = self.disp.keysym_to_keycode(XK.string_to_keysym(keysym))
        self._fake(X.KeyPress if down else X.KeyRelease, code)

    def tap(self, keysym: str, hold: float = 0.03) -> None:
        self.key(keysym, True)
        time.sleep(hold)
        self.key(keysym, False)
        time.sleep(0.3)

    def begin_drag(self) -> None:
        """Press on the source and move far enough for GTK and Quince to start a drag."""
        self.move(*DRAG_START)
        self.button(1, True)
        x0, y0 = DRAG_START
        x1, y1 = OVERLAY_AT
        for step in range(1, 11):
            self.move(x0 + (x1 - x0) * step // 10, y0 + (y1 - y0) * step // 10)
        self.wait_log("drag started")

    def hover_overlay(self) -> None:
        """Small moves so GTK sends XdndPosition to the overlay that just appeared."""
        x, y = OVERLAY_AT
        for d in (2, 4, 6, 8):
            self.move(x + d, y + d)
        self.wait_log("drag enter")


@pytest.fixture
def session(tmp_path):
    number = _free_display()
    command = _server_command(number)
    if command is None:
        pytest.skip("needs Xvfb (apt: xvfb), or Xephyr with QUINCE_NESTED_X=1")
    server = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not _wait_for(lambda: Path(f"/tmp/.X11-unix/X{number}").exists()):
        server.kill()
        pytest.fail("nested X server did not start")
    from Xlib import display

    disp = display.Display(f":{number}")
    s = Session(f":{number}", disp, tmp_path)
    try:
        s.start_spike()
        yield s
    finally:
        s.stop()
        disp.close()
        server.terminate()
        server.wait(5)


@pytest.fixture
def files(tmp_path):
    paths = [tmp_path / "one.png", tmp_path / "two.pdf"]
    for path in paths:
        path.write_text(f"content of {path.name}\n")
    return paths


needs_gtk = pytest.mark.skipif(not _has_system_gtk3(), reason="needs system python3-gi with GTK 3")


def assert_untouched(paths):
    for path in paths:
        assert path.read_text() == f"content of {path.name}\n"


@needs_gtk
def test_taps_switch_wheel_and_drop_with_shift_held_is_copy(session, files):
    session.start_source(*files)
    session.begin_drag()
    session.tap("Shift_L")
    session.wait_log("overlay 'convert' shown")
    session.tap("Shift_L", hold=0.6)  # too long: not a tap
    session.tap("Control_L")
    session.wait_log("tap Control_L -> tools")
    session.hover_overlay()

    session.key("Shift_L", True)  # Shift held at drop: GTK proposes Move
    time.sleep(0.2)
    session.button(1, False)
    session.key("Shift_L", False)

    session.wait_log("dropped on 'tools'")
    session.wait_source("DRAG END")
    assert f"dropped on 'tools': {[str(p) for p in files]}" in session.log
    assert session.log.count("tap Shift_L") == 1, "the long press must not count as a tap"
    assert "DRAG END copy" in session.source
    assert "DELETE REQUESTED" not in session.source
    assert_untouched(files)


@needs_gtk
def test_enter_drops_on_overlay(session, files):
    session.start_source(files[0])
    session.begin_drag()
    session.tap("Shift_L")
    session.hover_overlay()
    session.tap("Left")
    session.wait_log("key during drag: Left")
    session.tap("Return")
    session.wait_log(f"dropped on 'convert': ['{files[0]}']")
    session.button(1, False)
    session.wait_source("DRAG END")
    assert "DRAG END copy" in session.source
    assert "DELETE REQUESTED" not in session.source


@needs_gtk
@pytest.mark.parametrize("cancel_button", [3, 2])
def test_right_or_middle_click_cancels(session, files, cancel_button):
    session.start_source(files[0])
    session.begin_drag()
    session.tap("Alt_L")
    session.hover_overlay()
    session.button(cancel_button, True)
    session.button(cancel_button, False)
    session.wait_log("overlay cancelled")
    session.button(1, False)
    session.wait_source("DRAG END")
    time.sleep(0.5)
    assert "dropped on" not in session.log
    assert "DELETE REQUESTED" not in session.source
    assert_untouched(files[:1])


@needs_gtk
def test_escape_closes_overlay_and_cancels_drag(session, files):
    session.start_source(files[0])
    session.begin_drag()
    session.tap("Control_L")
    session.hover_overlay()
    session.tap("Escape")
    session.wait_log("overlay closed: escape")
    session.wait_source("DRAG FAILED")
    session.button(1, False)
    assert "dropped on" not in session.log


@needs_gtk
def test_scroll_during_drag_does_not_cancel(session, files):
    session.start_source(files[0])
    session.begin_drag()
    session.tap("Shift_L")
    session.hover_overlay()
    session.button(5, True)
    session.button(5, False)
    session.button(1, False)
    session.wait_log("dropped on 'convert'")
    assert "cancel" not in session.log


def test_button_drag_without_dnd_does_not_open_overlay(session):
    session.move(400, 300)
    session.button(1, True)
    for x in (410, 440, 480):
        session.move(x, 300)
    session.tap("Shift_L")
    session.button(1, False)
    time.sleep(0.5)
    assert "drag started" not in session.log
    assert "overlay" not in session.log
