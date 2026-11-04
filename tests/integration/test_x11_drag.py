"""End-to-end drag + tap + wheel + drop in a nested X server (Xvfb, or Xephyr when opted in).

``quince service`` runs against a GTK3 drag source that stands in for Nautilus 42 (same
drag-source code), so GTK's behavior during a drag is covered too: Enter drops at the
pointer, a right or middle click drops, Esc cancels, Shift held at the drop proposes Move.

Input is injected with XTest, which goes through the real input pipeline and produces
raw events (``xdotool mousemove`` warps the pointer and would not).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from quince.ui.wheel import geometry

pytestmark = pytest.mark.integration

HERE = Path(__file__).resolve().parent
SYSTEM_PYTHON = "/usr/bin/python3"
DRAG_START = (100, 100)
WHEEL_AT = (400, 300)  # where the pointer is when the trigger is tapped
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 24
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"


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
    """A nested X server running ``quince service`` and, optionally, a GTK drag source."""

    def __init__(self, display_name: str, disp, tmp_path: Path) -> None:
        self.disp = disp
        self.tmp_path = tmp_path
        self.env = {
            **os.environ,
            "DISPLAY": display_name,
            "QT_QPA_PLATFORM": "xcb",
            "GDK_BACKEND": "x11",
            "NO_AT_BRIDGE": "1",
            "QUINCE_INSTANCE": f"quince-test-{display_name.strip(':')}-{os.getpid()}",
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
        }
        self.env.pop("PYTHONPATH", None)
        self.service_log = tmp_path / "service.log"
        self.service: subprocess.Popen | None = None
        self._procs: list[subprocess.Popen] = []
        self._source_lines: list[str] = []

    # --- processes ----------------------------------------------------------

    def quince(self, *args: str, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "quince", *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
            **kwargs,
        )

    def start_service(self, *extra: str) -> None:
        with self.service_log.open("w") as log_file:
            self.service = subprocess.Popen(
                [sys.executable, "-m", "quince", "service", "-v", *extra],
                env=self.env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        self._procs.append(self.service)
        assert _wait_for(lambda: "Quince service running" in self.log), self.log

    def start_source(self, *paths: Path | str) -> None:
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
        return self.service_log.read_text()

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

    def move(self, x: float, y: float, pause: float = 0.06) -> None:
        from Xlib import X

        self._fake(X.MotionNotify, x=round(x), y=round(y))
        time.sleep(pause)

    def glide(self, x: float, y: float, steps: int = 8) -> None:
        """Move in small steps, so the drag source sends positions along the way."""
        pointer = self.disp.screen().root.query_pointer()
        x0, y0 = pointer.root_x, pointer.root_y
        for step in range(1, steps + 1):
            self.move(x0 + (x - x0) * step / steps, y0 + (y - y0) * step / steps)

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
        self.glide(*WHEEL_AT, steps=10)
        self.wait_log("drag started")

    def open_wheel(self, keysym: str, wheel: str) -> tuple[float, float, str]:
        """Tap a trigger; returns the wheel center and the logged contents."""
        self.tap(keysym)
        self.wait_log(f"wheel {wheel} opened at")
        match = re.search(rf"wheel {wheel} opened at (-?\d+),(-?\d+): (.*)", self.log)
        return float(match.group(1)), float(match.group(2)), match.group(3)

    def slice_point(self, center: tuple[float, float], count: int, index: int):
        s = geometry.layout(count).slices[index]
        return center[0] + s.label_x, center[1] + s.label_y


@pytest.fixture
def session(request, tmp_path):
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
        s.start_service(*getattr(request, "param", ()))
        yield s
    finally:
        s.stop()
        disp.close()
        server.terminate()
        server.wait(5)


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


@pytest.fixture
def images(tmp_path):
    return [_write(tmp_path / "one.png", PNG), _write(tmp_path / "two.png", PNG)]


@pytest.fixture
def mixed(tmp_path):
    return [_write(tmp_path / "one.png", PNG), _write(tmp_path / "two.pdf", PDF)]


needs_gtk = pytest.mark.skipif(not _has_system_gtk3(), reason="needs system python3-gi with GTK 3")


def assert_untouched(paths):
    for path in paths:
        assert path.read_bytes() == (PNG if path.suffix == ".png" else PDF)


@needs_gtk
def test_wheel_shows_registry_contents_switches_and_drop_with_shift_is_copy(session, images):
    session.start_source(*images)
    session.begin_drag()
    session.wait_log("dragged: 2 files, image (PNG)")  # read before any wheel is shown
    cx, cy, contents = session.open_wheel("Shift_L", "convert")
    assert contents == "JPG, WebP"
    session.tap("Shift_L", hold=0.6)  # too long: not a tap
    session.tap("Control_L")
    session.wait_log("wheel switched to tools: empty (Nothing available)")
    session.tap("Shift_L")
    session.wait_log("wheel switched to convert: JPG, WebP")

    session.glide(*session.slice_point((cx, cy), 2, 1))  # the WebP slice
    session.key("Shift_L", True)  # Shift held at drop: GTK proposes Move
    time.sleep(0.2)
    session.button(1, False)
    session.key("Shift_L", False)

    session.wait_log("chosen convert.image.webp for")
    session.wait_source("DRAG END")
    assert f"chosen convert.image.webp for {[str(p) for p in images]}" in session.log
    switches = session.log.count("controller: wheel switched")
    assert switches == 2, "the long press must not count as a tap"
    assert "DRAG END copy" in session.source
    assert "DELETE REQUESTED" not in session.source
    assert_untouched(images)


@needs_gtk
def test_keyboard_choice_without_moving_the_mouse(session, images):
    """The 1 px nudge makes the wheel the drop target, so GTK's Enter drop lands on it."""
    session.start_source(images[0])
    session.begin_drag()
    session.open_wheel("Shift_L", "convert")
    session.tap("Right")
    session.tap("Right")
    session.tap("Return")
    session.wait_log(f"chosen convert.image.webp for ['{images[0]}']")
    session.wait_source("DRAG END")
    assert "DRAG END copy" in session.source  # swallowed by the closed wheel
    session.tap("Shift_L")
    session.wait_log("tap convert ignored")  # one wheel per drag
    session.button(1, False)
    session.wait_log("drag ended")
    assert session.log.count("controller: chosen ") == 1
    assert "DELETE REQUESTED" not in session.source
    assert_untouched(images[:1])


@needs_gtk
@pytest.mark.parametrize("cancel_button", [3, 2])
def test_right_or_middle_click_cancels(session, images, cancel_button):
    session.start_source(images[0])
    session.begin_drag()
    session.open_wheel("Alt_L", "actions")
    session.button(cancel_button, True)
    session.button(cancel_button, False)
    session.wait_log("wheel closed: cancel click")
    session.button(1, False)
    session.wait_source("DRAG END")
    time.sleep(0.5)
    assert "chosen " not in session.log
    assert "DELETE REQUESTED" not in session.source
    assert_untouched(images[:1])


@needs_gtk
def test_escape_closes_wheel_and_cancels_drag(session, images):
    session.start_source(images[0])
    session.begin_drag()
    session.open_wheel("Control_L", "tools")
    session.tap("Escape")
    session.wait_log("wheel closed: escape")
    session.wait_source("DRAG FAILED")
    session.button(1, False)
    assert "chosen " not in session.log


@needs_gtk
def test_mixed_selection_gets_only_actions(session, mixed):
    session.start_source(*mixed)
    session.begin_drag()
    session.wait_log("dragged: 2 files, mixed types")
    _, _, contents = session.open_wheel("Shift_L", "convert")
    assert contents == "empty (Only file actions for folders and mixed files)"
    session.tap("Alt_L")
    session.wait_log("wheel switched to actions: Copy, Move, Copy Path,")
    match = re.search(r"wheel convert opened at (\d+),(\d+)", session.log)
    center = (float(match.group(1)), float(match.group(2)))
    actions = len(re.search(r"wheel switched to actions: (.*)", session.log).group(1).split(", "))
    session.glide(*session.slice_point(center, actions, 2))  # Copy Path
    session.button(1, False)
    session.wait_log("chosen action.copy_path for")
    session.wait_source("DRAG END copy")
    # File actions run on the GUI thread once the drag is over and the wheel is gone.
    session.wait_log("starting action.copy_path for 2 item(s)")


@needs_gtk
def test_drop_in_center_closes_without_choosing(session, images):
    session.start_source(images[0])
    session.begin_drag()
    cx, cy, _ = session.open_wheel("Shift_L", "convert")
    session.glide(cx + 3, cy + 3, steps=3)
    session.button(1, False)
    session.wait_log("wheel closed: dropped outside slices")
    time.sleep(0.3)
    assert "chosen " not in session.log


@needs_gtk
def test_scroll_during_drag_does_not_cancel(session, images):
    session.start_source(images[0])
    session.begin_drag()
    cx, cy, _ = session.open_wheel("Shift_L", "convert")
    session.button(5, True)
    session.button(5, False)
    session.glide(*session.slice_point((cx, cy), 2, 0))
    session.button(1, False)
    session.wait_log("chosen convert.image.jpg")
    assert "cancel" not in session.log


@needs_gtk
def test_text_drag_opens_no_wheel(session):
    """A text drag (editor, browser) offers no text/uri-list, so no wheel opens."""
    session.start_source("--text", "some selected text")
    session.begin_drag()
    session.wait_log("drag carries no local files")
    session.tap("Shift_L")
    session.wait_log("tap convert ignored")
    session.button(1, False)
    assert "opened at" not in session.log


def test_button_drag_without_dnd_opens_nothing(session):
    session.move(400, 300)
    session.button(1, True)
    for x in (410, 440, 480):
        session.move(x, 300)
    session.tap("Shift_L")
    session.button(1, False)
    time.sleep(0.5)
    assert "drag started" not in session.log
    assert "wheel" not in session.log


def test_single_instance_and_stop(session):
    second = session.quince("service")
    assert second.returncode == 0
    assert "already running" in second.stdout
    stopped = session.quince("service", "--stop")
    assert stopped.returncode == 0 and "stopped" in stopped.stdout
    assert session.service.wait(5) == 0
    assert "Quince service stopped" in session.log
    assert session.quince("service", "--stop").returncode == 1  # nothing running now


@needs_gtk
@pytest.mark.parametrize("session", [("--demo-ops",)], indirect=True)
def test_demo_jobs_from_the_wheel(session, images):
    """A job chosen on the wheel runs; success writes the output, failure leaves nothing."""
    session.start_source(images[0])  # the GTK source quits after one drag
    session.begin_drag()
    _, _, contents = session.open_wheel("Control_L", "tools")
    assert contents == "Slow Demo, Fail Demo"
    session.tap("Right")
    session.tap("Return")
    session.button(1, False)
    session.wait_log("job 1 succeeded", timeout=20)
    assert (images[0].parent / "one-demo.txt").exists()

    session.start_source(images[0])
    session.begin_drag()
    session.open_wheel("Control_L", "tools")
    session.tap("End")
    session.tap("Return")
    session.button(1, False)
    session.wait_log("job 2 failed: Demo failure", timeout=20)
    names = sorted(p.name for p in images[0].parent.iterdir() if p.is_file())
    assert names == ["one-demo.txt", "one.png", "service.log", "two.png"]  # no partial file
    assert (session.tmp_path / "state" / "quince" / "quince.log").exists()
