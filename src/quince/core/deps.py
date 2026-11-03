"""External dependency declarations and availability checks.

Required dependencies must be present for Quince to run at all. Optional ones only
enable some operations; when they are missing those operations are hidden.
"""

from __future__ import annotations

import ctypes.util
import importlib.metadata
import importlib.util
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

VERSION_TIMEOUT_S = 15
_VERSION_RE = re.compile(r"\d+(?:\.\d+)+")


class DepKind(Enum):
    BINARY = "binary"
    PYTHON = "python"
    LIBRARY = "library"


@dataclass(frozen=True)
class Dependency:
    """A single external dependency.

    ``target`` is the executable name (binary), import name (python) or the name
    passed to ``ctypes.util.find_library`` (library).
    """

    id: str
    kind: DepKind
    target: str
    required: bool
    enables: str
    apt: str | None = None
    pip: str | None = None
    version_args: tuple[str, ...] = ("--version",)


@dataclass(frozen=True)
class DependencyStatus:
    dependency: Dependency
    available: bool
    version: str | None = None
    location: str | None = None


DEPENDENCIES: tuple[Dependency, ...] = (
    # Python packages (installed into .venv from pyproject.toml)
    Dependency("pyside6", DepKind.PYTHON, "PySide6", True, "all UI", pip="PySide6"),
    Dependency("pillow", DepKind.PYTHON, "PIL", True, "image conversion and tools", pip="Pillow"),
    Dependency(
        "pillow-heif", DepKind.PYTHON, "pillow_heif", True, "HEIC/HEIF images", pip="pillow-heif"
    ),
    Dependency("pypdf", DepKind.PYTHON, "pypdf", True, "PDF merge, split, organize", pip="pypdf"),
    Dependency("pymupdf", DepKind.PYTHON, "pymupdf", True, "PDF rendering and text", pip="PyMuPDF"),
    Dependency("pysubs2", DepKind.PYTHON, "pysubs2", True, "subtitle conversion", pip="pysubs2"),
    Dependency(
        "python-xlib",
        DepKind.PYTHON,
        "Xlib",
        True,
        "global drag and trigger detection (X11)",
        pip="python-xlib",
    ),
    # System libraries
    Dependency(
        "libxcb-cursor",
        DepKind.LIBRARY,
        "xcb-cursor",
        True,
        "Qt 6 xcb platform plugin (X11)",
        apt="libxcb-cursor0",
    ),
    # Binaries
    Dependency(
        "ffmpeg",
        DepKind.BINARY,
        "ffmpeg",
        True,
        "audio and video conversion and tools",
        apt="ffmpeg",
        version_args=("-hide_banner", "-version"),
    ),
    Dependency(
        "ffprobe",
        DepKind.BINARY,
        "ffprobe",
        True,
        "media inspection, progress, metadata",
        apt="ffmpeg",
        version_args=("-hide_banner", "-version"),
    ),
    Dependency(
        "soffice",
        DepKind.BINARY,
        "soffice",
        False,
        "documents, slides and spreadsheets",
        apt="libreoffice",
    ),
    Dependency("pandoc", DepKind.BINARY, "pandoc", False, "Markdown and EPUB", apt="pandoc"),
    Dependency(
        "rsvg-convert", DepKind.BINARY, "rsvg-convert", False, "SVG input", apt="librsvg2-bin"
    ),
    Dependency("7z", DepKind.BINARY, "7z", False, "7z archives", apt="p7zip-full", version_args=()),
    Dependency("unrar", DepKind.BINARY, "unrar", False, "RAR input", apt="unrar", version_args=()),
)


def parse_version(text: str) -> str | None:
    """Return the first dotted version number found in ``text``."""
    for line in text.splitlines():
        if match := _VERSION_RE.search(line):
            return match.group(0)
    return None


def _run_version(path: str, args: tuple[str, ...]) -> str | None:
    try:
        proc = subprocess.run(
            [path, *args],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_version(proc.stdout + "\n" + proc.stderr)


def _check_binary(
    dep: Dependency, which: Callable[[str], str | None], versions: bool
) -> DependencyStatus:
    path = which(dep.target)
    if path is None:
        return DependencyStatus(dep, available=False)
    version = _run_version(path, dep.version_args) if versions else None
    return DependencyStatus(dep, True, version, path)


def _check_python(dep: Dependency, versions: bool) -> DependencyStatus:
    spec = importlib.util.find_spec(dep.target)
    if spec is None:
        return DependencyStatus(dep, available=False)
    if not versions:
        return DependencyStatus(dep, True, None, spec.origin)
    try:
        ver = importlib.metadata.version(dep.pip or dep.target)
    except importlib.metadata.PackageNotFoundError:
        ver = None
    return DependencyStatus(dep, True, ver, spec.origin)


def _check_library(dep: Dependency) -> DependencyStatus:
    found = ctypes.util.find_library(dep.target)
    return DependencyStatus(dep, found is not None, None, found)


def check(
    dep: Dependency,
    which: Callable[[str], str | None] = shutil.which,
    *,
    versions: bool = True,
) -> DependencyStatus:
    """Check whether a single dependency is available.

    With ``versions=False`` only presence is checked, which is fast: no process is run.
    """
    if dep.kind is DepKind.BINARY:
        return _check_binary(dep, which, versions)
    if dep.kind is DepKind.PYTHON:
        return _check_python(dep, versions)
    return _check_library(dep)


def check_all(
    deps: Iterable[Dependency] = DEPENDENCIES,
    which: Callable[[str], str | None] = shutil.which,
    *,
    versions: bool = True,
) -> list[DependencyStatus]:
    return [check(dep, which, versions=versions) for dep in deps]


def find(dep_id: str) -> Dependency | None:
    return next((d for d in DEPENDENCIES if d.id == dep_id), None)


def missing_apt_packages(statuses: Iterable[DependencyStatus]) -> list[str]:
    """Apt packages that would provide the missing dependencies, without duplicates."""
    packages: list[str] = []
    for status in statuses:
        apt = status.dependency.apt
        if not status.available and apt and apt not in packages:
            packages.append(apt)
    return packages


def install_hint(dep: Dependency) -> str:
    if dep.apt:
        return f"sudo apt install {dep.apt}"
    if dep.pip:
        return f"pip install {dep.pip}"
    return ""


class Availability:
    """Which dependencies are present, for deciding which operations can be offered."""

    def __init__(self, statuses: Iterable[DependencyStatus]) -> None:
        self._statuses: Mapping[str, DependencyStatus] = {s.dependency.id: s for s in statuses}

    @classmethod
    def probe(cls) -> Availability:
        """Check every declared dependency for presence (no version queries)."""
        return cls(check_all(versions=False))

    @classmethod
    def everything(cls) -> Availability:
        """Pretend every declared dependency is present (tests, previews)."""
        return cls(DependencyStatus(dep, True) for dep in DEPENDENCIES)

    def is_available(self, dep_id: str) -> bool:
        status = self._statuses.get(dep_id)
        return status is not None and status.available

    def missing(self, requires: Iterable[str]) -> list[str]:
        return [dep_id for dep_id in requires if not self.is_available(dep_id)]

    def reason(self, requires: Iterable[str]) -> str | None:
        """Why something with these requirements is unavailable, or None if it is not."""
        missing = self.missing(requires)
        if not missing:
            return None
        parts = []
        for dep_id in missing:
            dep = find(dep_id)
            hint = install_hint(dep) if dep else ""
            parts.append(f"{dep_id} ({hint})" if hint else dep_id)
        return "needs " + ", ".join(parts)
