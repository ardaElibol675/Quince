"""The service's windows besides the wheel: Home / Library, the tray icon and wheels opened
without a drag. ``quince.app`` creates one ``Shell`` and routes the socket commands to it.

Commands (one line each, see ``quince.app``):
- ``home`` / ``library``: show the window on that page.
- ``open {"paths": [...], "wheel": "convert" | null}``: open a wheel at the pointer for these
  files (the Nautilus menu and ``quince open``).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QObject, QPoint
from PySide6.QtGui import QIcon

from quince.core import config
from quince.core.registry import Registry
from quince.integrations.install import icon_path
from quince.ui.home.window import MainWindow
from quince.ui.library.model import LibraryModel
from quince.ui.tray import Tray
from quince.ui.wheel.direct import DirectWheel
from quince.ui.wheel.model import WHEEL_TITLES
from quince.ui.wheel.window import WheelWindow

log = logging.getLogger(__name__)


def app_icon() -> QIcon:
    return QIcon(str(icon_path()))


def parse_open(arg: str) -> tuple[list[str], str | None]:
    """The paths and wheel of an ``open`` command; ValueError when malformed."""
    try:
        data = json.loads(arg)
    except ValueError as exc:
        raise ValueError(f"bad open request: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError("bad open request: not an object")
    paths = data.get("paths")
    wheel = data.get("wheel")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        raise ValueError("bad open request: no paths")
    if not all(os.path.isabs(p) for p in paths):
        raise ValueError("bad open request: paths must be absolute")
    if wheel is not None and wheel not in WHEEL_TITLES:
        raise ValueError(f"bad open request: unknown wheel {wheel!r}")
    return paths, wheel


def open_request(paths: Sequence[str | Path], wheel: str | None = None) -> str:
    """The ``open`` command line for ``paths``."""
    return "open " + json.dumps({"paths": [str(p) for p in paths], "wheel": wheel})


class Shell(QObject):
    def __init__(
        self,
        wheel: WheelWindow,
        registry: Registry,
        library: LibraryModel,
        *,
        run_operation: Callable[[object, object], None],
        drag_busy: Callable[[], bool],
        notify: Callable[[str], None],
        quit_app: Callable[[], None],
        load_config: Callable[[], config.Config] = config.load,
        tray: bool = True,
    ) -> None:
        super().__init__()
        self.library = library
        self._load_config = load_config
        self.direct = DirectWheel(
            wheel, registry, load_config=load_config, busy=drag_busy, notify=notify
        )
        self.direct.operation_chosen.connect(run_operation)
        self._window: MainWindow | None = None
        self.tray: Tray | None = None
        if tray:
            self.tray = Tray(
                app_icon(), on_home=self.show_home, on_library=self.show_library, on_quit=quit_app
            )
            self.tray.show()

    @property
    def window(self) -> MainWindow:
        if self._window is None:
            self._window = MainWindow(self.library)
            self._window.setWindowIcon(app_icon())
            self._window.wheel_requested.connect(self._on_wheel_requested)
        return self._window

    def show_home(self) -> None:
        self.window.configure(self._load_config())
        self.window.show_home()

    def show_library(self) -> None:
        self.window.configure(self._load_config())
        self.window.show_library()

    def open_files(
        self, paths: Sequence[str | Path], wheel: str | None = None, pos: QPoint | None = None
    ) -> bool:
        return self.direct.open(paths, wheel=wheel, pos=pos)

    # --- socket commands ---------------------------------------------------------------

    def handlers(self) -> dict[str, Callable[[str], str]]:
        return {"home": self._cmd_home, "library": self._cmd_library, "open": self._cmd_open}

    def _cmd_home(self, _arg: str) -> str:
        self.show_home()
        return "ok"

    def _cmd_library(self, _arg: str) -> str:
        self.show_library()
        return "ok"

    def _cmd_open(self, arg: str) -> str:
        try:
            paths, wheel = parse_open(arg)
        except ValueError as exc:
            return f"error: {exc}"
        if not self.open_files(paths, wheel):
            return "error: none of the files exists"
        return "ok"

    def _on_wheel_requested(self, paths: list, wheel: str | None, at: QPoint) -> None:
        self.open_files(paths, wheel, at)
