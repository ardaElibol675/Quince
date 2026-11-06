"""Quince for Nautilus: "Open in Quince" in the context menu of files and folders.

Installed by the quince package into /usr/share/nautilus-python/extensions/ (in a development
setup by ``quince integrate`` into ~/.local/share/nautilus-python/extensions/). It runs inside
Nautilus (system Python with nautilus-python, package ``python3-nautilus``) and never
imports Quince: it starts ``quince open`` with the selected paths, which opens the wheel at the
pointer (starting the Quince service when it is not running).

Works with the nautilus-python API 3.0 (Nautilus 42, Ubuntu 22.04) and 4.0 (Nautilus 43+).
"""

import contextlib
import os
import subprocess

import gi

try:
    gi.require_version("Nautilus", "4.0")
except ValueError:
    gi.require_version("Nautilus", "3.0")
from gi.repository import GObject, Nautilus

# Filled in by ``quince integrate``: the command that runs Quince.
QUINCE_COMMAND = ["quince"]


def _local_paths(files):
    """Paths of the selected items; None when any of them is not a local file."""
    paths = []
    for item in files:
        location = item.get_location()
        path = location.get_path() if location is not None else None
        if not path:
            return None
        paths.append(path)
    return paths


def _environment():
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def open_in_quince(paths):
    # OSError: Quince was removed while Nautilus still had this extension loaded.
    with contextlib.suppress(OSError):
        subprocess.Popen(
            [*QUINCE_COMMAND, "open", "--", *paths],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=_environment(),
        )


class QuinceMenuProvider(GObject.GObject, Nautilus.MenuProvider):
    def get_file_items(self, *args):
        # API 3.0 passes (window, files), API 4.0 passes (files).
        files = args[-1]
        paths = _local_paths(files) if files else None
        if not paths:
            return []
        item = Nautilus.MenuItem(
            name="Quince::open",
            label="Open in Quince",
            tip="Open the Quince wheel for the selected items",
        )
        item.connect("activate", self._activate, paths)
        return [item]

    def get_background_items(self, *args):
        return []

    def _activate(self, _item, paths):
        open_in_quince(paths)
