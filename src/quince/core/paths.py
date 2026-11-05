"""XDG base directories for Quince's config, data and state."""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR = "quince"


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var, "")
    # The XDG spec says relative paths in these variables are invalid and must be ignored.
    base = Path(value) if value and os.path.isabs(value) else Path.home() / fallback
    return base / APP_DIR


def data_home() -> Path:
    """The user's XDG data folder itself (``~/.local/share``), for desktop integration."""
    return _xdg("XDG_DATA_HOME", ".local/share").parent


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state")


def config_file() -> Path:
    return config_dir() / "config.toml"


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache")
