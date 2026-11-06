"""Start at login: an autostart entry that starts ``quince service``.

The files themselves are the setting (Settings → General), as the freedesktop autostart spec
intends, so other tools (GNOME Tweaks, "Startup Applications") see and change the same thing:

- ``~/.config/autostart/quince.desktop`` (the user's entry) wins when it exists;
- otherwise an entry in a system folder (``$XDG_CONFIG_DIRS/autostart``, by default
  ``/etc/xdg/autostart``, installed by the .deb) applies.

An entry that says ``Hidden=true`` or ``X-GNOME-Autostart-enabled=false`` counts as off. So
with the package installed, turning the setting off writes a user entry with ``Hidden=true``,
and turning it on again removes that entry. Entries carry ``TryExec``, so a login after the
package was removed starts nothing.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from pathlib import Path

from quince.integrations.install import APP_ID, exec_line, quince_command, write_file

log = logging.getLogger(__name__)


def config_home() -> Path:
    value = os.environ.get("XDG_CONFIG_HOME", "")
    return Path(value) if value and os.path.isabs(value) else Path.home() / ".config"


def config_dirs() -> list[Path]:
    """``$XDG_CONFIG_DIRS`` (only absolute entries), by default ``/etc/xdg``."""
    value = os.environ.get("XDG_CONFIG_DIRS", "")
    dirs = [Path(p) for p in value.split(os.pathsep) if p and os.path.isabs(p)]
    return dirs or [Path("/etc/xdg")]


def autostart_file(base: Path | None = None) -> Path:
    return (base or config_home()) / "autostart" / f"{APP_ID}.desktop"


def system_autostart_file(dirs: Sequence[Path] | None = None) -> Path | None:
    """The first system entry for Quince, if any."""
    for folder in dirs if dirs is not None else config_dirs():
        path = autostart_file(folder)
        if path.is_file():
            return path
    return None


def autostart_entry(command: Sequence[str]) -> str:
    return f"""[Desktop Entry]
Type=Application
Name=Quince
Comment=Quince background service: the drag-and-tap wheel
TryExec={exec_line(command[:1])}
Exec={exec_line(command, "service")}
Icon={APP_ID}
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
"""


DISABLED_ENTRY = f"""[Desktop Entry]
Type=Application
Name=Quince
Comment=Quince does not start at login (Settings → General)
Exec={APP_ID} service
Hidden=true
"""


def _keys(text: str) -> dict[str, str]:
    keys = {}
    for line in text.splitlines():
        if line.startswith("[") and keys:
            break  # only the [Desktop Entry] group counts
        key, sep, value = line.partition("=")
        if sep:
            keys[key.strip()] = value.strip()
    return keys


def _off(text: str) -> bool:
    keys = {k: v.lower() for k, v in _keys(text).items()}
    return keys.get("Hidden") == "true" or keys.get("X-GNOME-Autostart-enabled") == "false"


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _entry_on(path: Path) -> bool:
    text = _read(path)
    return text is not None and not _off(text)


def foreign_user_entry(command: Sequence[str], base: Path | None = None) -> Path | None:
    """The user's entry when it is on but starts another Quince than ``command`` (e.g. the
    Python of an earlier development setup), else None."""
    path = autostart_file(base)
    text = _read(path)
    if text is None or _off(text):
        return None
    return None if _keys(text).get("Exec") == exec_line(command, "service") else path


def is_enabled(base: Path | None = None, system_dirs: Sequence[Path] | None = None) -> bool:
    user = autostart_file(base)
    if user.exists():
        return _entry_on(user)
    system = system_autostart_file(system_dirs)
    return system is not None and _entry_on(system)


def set_enabled(
    enabled: bool,
    *,
    command: Sequence[str] | None = None,
    base: Path | None = None,
    system_dirs: Sequence[Path] | None = None,
) -> Path:
    """Make Quince start (or not) at login. Returns the user entry's path."""
    path = autostart_file(base)
    system = system_autostart_file(system_dirs)
    system_on = system is not None and _entry_on(system)
    if enabled and system_on:
        path.unlink(missing_ok=True)  # the system entry applies again
    elif enabled:
        write_file(path, autostart_entry(list(command or quince_command())))
    elif system is not None:
        write_file(path, DISABLED_ENTRY)  # hides the system entry for this user
    else:
        path.unlink(missing_ok=True)
    log.info("Quince %s at login", "starts" if enabled else "no longer starts")
    return path
