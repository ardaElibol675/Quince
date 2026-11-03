"""Quince: offline file converter and file toolbox for Linux."""

import os
import sys
from importlib.metadata import PackageNotFoundError, version


def strip_foreign_paths(sys_path: list[str], pythonpath: str) -> list[str]:
    """``sys_path`` without the entries that came from ``PYTHONPATH``."""
    foreign = {os.path.abspath(p) for p in pythonpath.split(os.pathsep) if p}
    return [p for p in sys_path if not p or os.path.abspath(p) not in foreign]


def _ignore_foreign_pythonpath() -> None:
    """Keep a foreign PYTHONPATH (e.g. ROS from ~/.bashrc) from shadowing .venv packages.

    Runs on the first import of ``quince``, before PySide6, Pillow & co. are imported, and
    removes the variable so child processes do not inherit it either.
    ``QUINCE_KEEP_PYTHONPATH=1`` turns this off.
    """
    pythonpath = os.environ.get("PYTHONPATH")
    if not pythonpath or os.environ.get("QUINCE_KEEP_PYTHONPATH") == "1":
        return
    sys.path[:] = strip_foreign_paths(sys.path, pythonpath)
    del os.environ["PYTHONPATH"]


_ignore_foreign_pythonpath()

try:
    __version__ = version("quince")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0+unknown"
