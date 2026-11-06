"""Start at login: ``~/.config/autostart/quince.desktop`` starts ``quince service``.

The file itself is the setting (Settings → General), as the freedesktop autostart spec
intends: other tools (GNOME Tweaks, "Startup Applications") see and change the same file.
A file that exists but says ``Hidden=true`` or ``X-GNOME-Autostart-enabled=false`` counts
as off.
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


def autostart_file(base: Path | None = None) -> Path:
    return (base or config_home()) / "autostart" / f"{APP_ID}.desktop"


def autostart_entry(command: Sequence[str]) -> str:
    return f"""[Desktop Entry]
Type=Application
Name=Quince
Comment=Quince background service: the drag-and-tap wheel
Exec={exec_line(command, "service")}
Icon={APP_ID}
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
"""


def _off(text: str) -> bool:
    keys = {}
    for line in text.splitlines():
        if line.startswith("[") and keys:
            break  # only the [Desktop Entry] group counts
        key, sep, value = line.partition("=")
        if sep:
            keys[key.strip()] = value.strip().lower()
    return keys.get("Hidden") == "true" or keys.get("X-GNOME-Autostart-enabled") == "false"


def is_enabled(base: Path | None = None) -> bool:
    try:
        text = autostart_file(base).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return not _off(text)


def set_enabled(
    enabled: bool, *, command: Sequence[str] | None = None, base: Path | None = None
) -> Path:
    """Write (or remove) the autostart entry. Returns its path."""
    path = autostart_file(base)
    if enabled:
        write_file(path, autostart_entry(list(command or quince_command())))
        log.info("Quince starts at login (%s)", path)
    else:
        path.unlink(missing_ok=True)
        log.info("Quince no longer starts at login")
    return path
