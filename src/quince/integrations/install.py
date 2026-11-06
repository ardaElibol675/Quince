"""``quince integrate``: the menu entry, the icon and the Nautilus extension for this user.

Everything goes into the user's XDG data folder (``~/.local/share``); nothing needs root.
The ``.deb`` package (Phase 18) installs the same files system-wide instead.

| File | For |
|------|-----|
| ``applications/quince.desktop`` | the app menu entry (opens Home); Library, Settings, Quit |
| ``icons/hicolor/scalable/apps/quince.svg`` | its icon |
| ``nautilus-python/extensions/quince_nautilus.py`` | "Open in Quince" in Nautilus |
"""

from __future__ import annotations

import glob
import logging
import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from quince.core import paths

log = logging.getLogger(__name__)

APP_ID = "quince"
NAUTILUS_PYTHON_GLOB = "/usr/lib/*/nautilus/extensions-*/libnautilus-python.so"
_MARK = "# Installed by `quince integrate`; `quince integrate --remove` deletes it.\n"


def quince_command() -> list[str]:
    """How other programs start this Quince: its own Python with ``-m quince``."""
    return [sys.executable, "-m", "quince"]


def data_file(name: str) -> str:
    return resources.files("quince.integrations").joinpath("data", name).read_text("utf-8")


def icon_path() -> Path:
    """The packaged icon file (for windows and the tray)."""
    return Path(str(resources.files("quince.integrations").joinpath("data", "quince.svg")))


def _exec_arg(arg: str) -> str:
    """Quote one argument for an ``Exec`` key (Desktop Entry spec)."""
    if arg and not any(c in arg for c in " \t\n\"'\\><~|&;$*?#()`%="):
        return arg
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return f'"{escaped}"'.replace("%", "%%")


def exec_line(command: Sequence[str], *args: str) -> str:
    return " ".join(_exec_arg(a) for a in (*command, *args))


def desktop_entry(command: Sequence[str]) -> str:
    return f"""[Desktop Entry]
Type=Application
Name=Quince
GenericName=File Converter
Comment=Convert files and use file tools with a drag-and-tap wheel
Exec={exec_line(command, "home")}
Icon={APP_ID}
Terminal=false
Categories=Utility;
Keywords=convert;converter;file;wheel;pdf;image;video;
StartupNotify=false
StartupWMClass={APP_ID}
Actions=library;settings;quit;

[Desktop Action library]
Name=Library
Exec={exec_line(command, "library")}

[Desktop Action settings]
Name=Settings
Exec={exec_line(command, "settings")}

[Desktop Action quit]
Name=Quit Quince
Exec={exec_line(command, "service", "--stop")}
"""


def nautilus_extension(command: Sequence[str]) -> str:
    text = data_file("quince_nautilus.py")
    placeholder = 'QUINCE_COMMAND = ["quince"]'
    if placeholder not in text:
        raise RuntimeError("the Nautilus extension template has no command placeholder")
    return _MARK + text.replace(placeholder, f"QUINCE_COMMAND = {list(command)!r}")


@dataclass(frozen=True)
class Target:
    path: Path
    content: str


def targets(command: Sequence[str] | None = None, data_home: Path | None = None) -> list[Target]:
    command = list(command or quince_command())
    home = data_home or paths.data_home()
    return [
        Target(home / "applications" / f"{APP_ID}.desktop", desktop_entry(command)),
        Target(
            home / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg",
            data_file("quince.svg"),
        ),
        Target(
            home / "nautilus-python" / "extensions" / "quince_nautilus.py",
            nautilus_extension(command),
        ),
    ]


def write_file(path: Path, content: str) -> None:
    """Write ``content`` atomically, readable by everyone (0644)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def install(command: Sequence[str] | None = None, data_home: Path | None = None) -> list[Path]:
    """Write every file (replacing an older version). Returns the paths written."""
    written = []
    for target in targets(command, data_home):
        write_file(target.path, target.content)
        written.append(target.path)
        log.info("installed %s", target.path)
    return written


def uninstall(data_home: Path | None = None) -> list[Path]:
    """Remove the files ``install`` wrote. Returns the paths removed."""
    removed = []
    for target in targets(["quince"], data_home):
        if target.path.exists():
            target.path.unlink()
            removed.append(target.path)
            log.info("removed %s", target.path)
    return removed


def nautilus_python_available() -> bool:
    return bool(glob.glob(NAUTILUS_PYTHON_GLOB))
