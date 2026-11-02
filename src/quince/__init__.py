"""Quince: offline file converter and file toolbox for Linux."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("quince")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0+unknown"
