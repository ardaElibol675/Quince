"""Build the Quince .deb for Ubuntu 22.04: ``python scripts/build_deb.py``.

Run it with the project's virtualenv (it renders the PNG icons with PySide6). The result is
``dist/quince_<version>_<arch>.deb``; install it with ``sudo apt install ./dist/quince_*.deb``.

What goes into the package:

- ``/opt/quince``: a virtualenv on the system ``python3.10`` with Quince and the exact Python
  packages of ``requirements.lock``; PySide6 without the Qt modules Quince does not use
- ``/usr/bin/quince``: the launcher (``packaging/deb/quince``)
- ``/usr/share/applications/quince.desktop``: the app menu entry (Home; Library, Settings, Quit)
- ``/etc/xdg/autostart/quince.desktop``: starts the service at login (Settings → General turns
  it off per user)
- ``/usr/share/icons/hicolor/*/apps/quince.*``: the icon, SVG and PNGs
- ``/usr/share/nautilus-python/extensions/quince_nautilus.py``: "Open in Quince" in Nautilus
- ``/usr/share/doc/quince/``: README and the third-party notice (``copyright``)

The package depends on the system libraries the bundled binaries link against (found with
``readelf`` and ``dpkg -S``, like ``dpkg-shlibdeps``), recommends the optional tools and stops
every running Quince before it is removed or upgraded (``packaging/deb/prerm``).

Building needs ``python3.10-venv``, ``dpkg-deb``, ``readelf`` (binutils) and, unless pip's cache
already has them, network access to download the Python packages once. Quince itself never
uses the network.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import textwrap
from collections.abc import Iterable, Iterator
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.append(str(ROOT / "src"))  # when Quince is not installed in this Python

from quince.integrations import autostart, install  # noqa: E402

PACKAGE = "quince"
PREFIX = install.INSTALL_PREFIX  # /opt/quince
PYTHON = "/usr/bin/python3.10"
MAINTAINER = "Arda Elibol <arda.elibol34@gmail.com>"
BUILD_DIR = ROOT / "packaging" / "deb" / "build"
DIST_DIR = ROOT / "dist"
ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)

# The Python bindings Quince imports (QtSvg: SVG icons and images).
PYSIDE_MODULES = {"QtCore", "QtGui", "QtWidgets", "QtDBus", "QtMultimedia", "QtNetwork", "QtSvg"}
# Qt plugin folders Quince needs; for the platforms only X11 and the headless ones (Wayland is
# not supported). Everything else, and every Qt library none of these link to, is removed.
QT_PLUGINS = {
    "platforms": {"libqxcb.so", "libqoffscreen.so", "libqminimal.so"},
    "xcbglintegrations": None,
    "platformthemes": None,
    "platforminputcontexts": {
        "libcomposeplatforminputcontextplugin.so",
        "libibusplatforminputcontextplugin.so",
    },
    "imageformats": {
        "libqgif.so",
        "libqico.so",
        "libqjpeg.so",
        "libqsvg.so",
        "libqtga.so",
        "libqtiff.so",
        "libqwbmp.so",
        "libqwebp.so",
    },
    "iconengines": None,
    "multimedia": None,
    "generic": None,
    "tls": None,
    "networkinformation": None,
}
# Inside PySide6/Qt: only lib/ and plugins/ are used at run time.
QT_KEEP_DIRS = {"lib", "plugins"}
# PySide6 developer tools and files (designer, qmlls, stubs, headers, ...).
PYSIDE_KEEP_SUFFIXES = (".py",)
# Python folders in PySide6/ that only its developer tools use (pyside6-deploy & co.).
PYSIDE_TOOL_DIRS = {"scripts"}

# Optional tools: each enables some operations (docs/DEPENDENCIES.md).
RECOMMENDS = [
    "libreoffice-writer",
    "libreoffice-calc",
    "libreoffice-impress",
    "pandoc",
    "librsvg2-bin",
    "p7zip-full",
    "ghostscript",
    "zbar-tools",
    "tesseract-ocr",
    "tesseract-ocr-tur",
    "python3-nautilus",
    "xdg-utils",
]
SUGGESTS = ["unrar"]  # multiverse (non-free)
# Programs Quince runs; libraries are found from the binaries.
DEPENDS = ["python3.10", "ffmpeg", "util-linux", "procps"]

DESCRIPTION = """\
offline file converter and toolbox with a drag-and-tap wheel
 Start dragging files in the file manager, tap Shift, Ctrl or Alt, and a
 radial wheel opens at the pointer: Convert (images, audio, video,
 documents, slides, spreadsheets, PDF, subtitles, archives), Tools
 (compress, editors, merge, ...) and Actions (copy, move, open with, ...).
 Drop the files on a slice; the result is always a new file.
 .
 Everything runs locally, without network access. The global wheel needs
 an X11 session (GNOME on Xorg on Ubuntu 22.04)."""


# --- pure helpers (unit tested) -------------------------------------------------------


def project_version(pyproject: Path = ROOT / "pyproject.toml") -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.MULTILINE)
    if not match:
        raise SystemExit(f"no version in {pyproject}")
    return match.group(1)


def control_file(
    version: str,
    arch: str,
    installed_kb: int,
    depends: Iterable[str],
    recommends: Iterable[str] = RECOMMENDS,
    suggests: Iterable[str] = SUGGESTS,
) -> str:
    return (
        f"Package: {PACKAGE}\n"
        f"Version: {version}\n"
        f"Architecture: {arch}\n"
        f"Maintainer: {MAINTAINER}\n"
        f"Installed-Size: {installed_kb}\n"
        f"Depends: {', '.join(sorted(set(depends)))}\n"
        f"Recommends: {', '.join(recommends)}\n"
        f"Suggests: {', '.join(suggests)}\n"
        "Section: utils\n"
        "Priority: optional\n"
        f"Description: {DESCRIPTION}\n"
    )


def keep_plugin(folder: str, name: str) -> bool:
    if folder not in QT_PLUGINS:
        return False
    wanted = QT_PLUGINS[folder]
    return wanted is None or name in wanted


def keep_pyside_file(name: str) -> bool:
    """Files directly in PySide6/: the wanted bindings, libpyside and the Python code."""
    if name.endswith(".abi3.so"):
        return name.removesuffix(".abi3.so") in PYSIDE_MODULES
    if name.startswith("libpyside6.abi3.so"):
        return True
    return name.endswith(PYSIDE_KEEP_SUFFIXES)


def parse_dynamic(readelf_output: str) -> tuple[list[str], list[str]]:
    """``readelf -d`` → (NEEDED sonames, RUNPATH/RPATH entries)."""
    needed, runpath = [], []
    for line in readelf_output.splitlines():
        match = re.search(r"\((NEEDED|RUNPATH|RPATH)\)\s+.*?\[(.*)\]", line)
        if not match:
            continue
        if match.group(1) == "NEEDED":
            needed.append(match.group(2))
        else:
            runpath += [p for p in match.group(2).split(":") if p]
    return needed, runpath


def parse_ldconfig(output: str, arch_tag: str = "x86-64") -> dict[str, str]:
    """``ldconfig -p`` → {soname: path} for this architecture (first entry wins)."""
    found: dict[str, str] = {}
    for line in output.splitlines():
        match = re.match(r"\s*(\S+)\s+\(([^)]*)\)\s+=>\s+(\S+)", line)
        if match and arch_tag in match.group(2):
            found.setdefault(match.group(1), match.group(3))
    return found


def parse_dpkg_search(output: str) -> set[str]:
    """``dpkg -S path`` → package names (without ``:arch``)."""
    packages = set()
    for line in output.splitlines():
        names, sep, _path = line.partition(": ")
        if sep and not names.startswith("diversion"):
            packages |= {n.strip().split(":")[0] for n in names.split(",")}
    return packages


def license_of(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression
    value = (meta.get("License") or "").strip()
    if value and "\n" not in value and len(value) < 80:
        return value
    classifiers = [
        c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License")
    ]
    return ", ".join(classifiers) or "see the package's license files"


def copyright_file(packages: Iterable[tuple[str, str, str]]) -> str:
    rows = "\n".join(f" {name} {version}: {lic}" for name, version, lic in sorted(packages))
    return f"""\
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Quince
Comment:
 Quince is not published under a license yet; this package is for personal use.

Files: *
Copyright: 2026 Arda Elibol
License: none
 No license has been chosen yet. All rights reserved.

Files: opt/quince/lib/*
Comment:
 Python packages bundled in /opt/quince, with the license each declares.
 Their full license texts are in the *.dist-info folders next to them.
 PySide6 bundles Qt 6 (LGPL-3.0) and FFmpeg (LGPL-2.1+) libraries;
 pillow-heif bundles libheif and libde265 (LGPL-3.0) and x265 (GPL-2.0+).
 PyMuPDF and pymupdf4llm are AGPL-3.0: distributing this package to others
 means following the AGPL for it as a whole.
 .
{rows}
License: various
 See above.
"""


# --- build steps ----------------------------------------------------------------------


def run(*cmd: str | Path, **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(map(str, cmd)), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def output(*cmd: str | Path) -> str:
    return subprocess.run([str(c) for c in cmd], check=True, capture_output=True, text=True).stdout


def site_packages(venv: Path) -> Path:
    [path] = (venv / "lib").glob("python3.*/site-packages")
    return path


def make_venv(venv: Path, work: Path) -> None:
    """A virtualenv with Quince (from a clean copy of the sources) and its locked packages."""
    run(PYTHON, "-m", "venv", venv)
    source = work / "source"
    shutil.copytree(ROOT / "src", source / "src", ignore=shutil.ignore_patterns(
        "__pycache__", "*.egg-info"))  # fmt: skip
    for name in ("pyproject.toml", "README.md"):
        shutil.copy2(ROOT / name, source / name)
    pip = [venv / "bin" / "python", "-m", "pip", "--disable-pip-version-check"]
    run(*pip, "install", "--no-warn-script-location", "-c", ROOT / "requirements.lock", source)
    run(*pip, "uninstall", "-y", "pip", "setuptools")
    for entry in (venv / "bin").iterdir():
        if not entry.name.startswith("python"):
            entry.unlink()  # scripts and activate files carry the build path


def prune_pyside(pyside: Path) -> None:
    for entry in pyside.iterdir():
        if entry.is_dir():
            tool = entry.name in PYSIDE_TOOL_DIRS or not (entry / "__init__.py").exists()
            if entry.name != "Qt" and tool:
                shutil.rmtree(entry)  # designer, typesystems, include, glue, scripts, ...
        elif not keep_pyside_file(entry.name):
            entry.unlink()
    qt = pyside / "Qt"
    for entry in qt.iterdir():
        if entry.name not in QT_KEEP_DIRS:
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
    for folder in (qt / "plugins").iterdir():
        for plugin in folder.iterdir():
            if not keep_plugin(folder.name, plugin.name):
                plugin.unlink()
        if not any(folder.iterdir()):
            folder.rmdir()
    # Qt libraries: keep only what the kept bindings and plugins load.
    libs = {p.name: p for p in (qt / "lib").iterdir()}
    roots = [p for p in pyside.iterdir() if p.is_file() and ".so" in p.name]
    roots += [p for p in (qt / "plugins").rglob("*.so")]
    wanted: set[str] = set()
    todo = [needed for root in roots for needed in elf_dynamic(root)[0]]
    while todo:
        name = todo.pop()
        if name in wanted or name not in libs:
            continue
        wanted.add(name)
        todo += elf_dynamic(libs[name].resolve())[0]
    for name, path in libs.items():
        if name not in wanted and not (path.is_symlink() and os.readlink(path) in wanted):
            path.unlink()


def is_elf(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as f:
        return f.read(4) == b"\x7fELF"


def elf_dynamic(path: Path) -> tuple[list[str], list[str]]:
    result = subprocess.run(["readelf", "-d", str(path)], capture_output=True, text=True)
    return parse_dynamic(result.stdout)


def walk_files(root: Path) -> Iterator[Path]:
    for folder, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            yield Path(folder) / name


def system_depends(stage: Path) -> tuple[set[str], set[str]]:
    """(packages providing the system libraries the bundled binaries need, unresolved sonames)."""
    bundled = {p.name for p in walk_files(stage)}
    needed: set[str] = set()
    for path in walk_files(stage):
        if is_elf(path):
            needed |= set(elf_dynamic(path)[0])
    system = parse_ldconfig(output("/sbin/ldconfig", "-p"))
    # The loader also finds names the cache leaves out (libbz2.so.1) in the same folders.
    lib_dirs = sorted({os.path.dirname(p) for p in system.values()})
    packages, unresolved = set(), set()
    for soname in sorted(needed - bundled):
        path = system.get(soname) or next(
            (f"{d}/{soname}" for d in lib_dirs if os.path.exists(f"{d}/{soname}")), None
        )
        if path is None:
            unresolved.add(soname)
            continue
        candidates = {path, os.path.realpath(path)}
        candidates |= {re.sub(r"^/usr/lib/", "/lib/", c) for c in list(candidates)}
        candidates |= {re.sub(r"^/lib/", "/usr/lib/", c) for c in list(candidates)}
        found = subprocess.run(["dpkg", "-S", *sorted(candidates)], capture_output=True, text=True)
        owners = parse_dpkg_search(found.stdout)
        if owners:
            packages |= owners
        else:
            unresolved.add(soname)
    return packages, unresolved


def render_icons(svg: Path, share: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    _app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(svg))
    for size in ICON_SIZES:
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        target = share / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{PACKAGE}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not image.save(str(target)):
            raise SystemExit(f"could not write {target}")
    scalable = share / "icons" / "hicolor" / "scalable" / "apps" / f"{PACKAGE}.svg"
    scalable.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(svg, scalable)


def write(path: Path, content: str | bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    path.chmod(mode)


def desktop_files(stage: Path) -> None:
    share = stage / "usr" / "share"
    write(stage / "usr" / "bin" / PACKAGE, (ROOT / "packaging/deb/quince").read_text(), 0o755)
    write(share / "applications" / f"{PACKAGE}.desktop", install.desktop_entry([PACKAGE]))
    write(
        stage / "etc" / "xdg" / "autostart" / f"{PACKAGE}.desktop",
        autostart.autostart_entry([PACKAGE]),
    )
    write(
        share / "nautilus-python" / "extensions" / "quince_nautilus.py",
        install.nautilus_extension(
            [str(install.LAUNCHER)], header="# Installed by the quince package.\n"
        ),
    )
    render_icons(install.icon_path(), share)
    write(share / "doc" / PACKAGE / "README.md", (ROOT / "README.md").read_text())


def notice(venv: Path) -> str:
    dists = metadata.distributions(path=[str(site_packages(venv))])
    rows = {(d.metadata["Name"], d.version, license_of(d)) for d in dists}
    return copyright_file(r for r in rows if r[0].lower() != PACKAGE)


def compile_python(stage: Path, venv: Path) -> None:
    for cache in list(venv.rglob("__pycache__")):
        shutil.rmtree(cache)
    run(
        PYTHON, "-m", "compileall", "-q", "-j", "0",
        "--invalidation-mode", "unchecked-hash",
        "-s", stage, "-p", "/", site_packages(venv),
    )  # fmt: skip


def normalize_modes(stage: Path) -> None:
    for folder, dirs, files in os.walk(stage):
        for name in dirs:
            os.chmod(os.path.join(folder, name), 0o755)
        for name in files:
            path = os.path.join(folder, name)
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                continue
            os.chmod(path, 0o755 if st.st_mode & 0o111 else 0o644)
    stage.chmod(0o755)


def smoke_test(venv: Path) -> None:
    """The pruned virtualenv imports every Quince module and finds what Qt needs."""
    code = textwrap.dedent(
        """
        import importlib, pkgutil, sys, quince
        for info in pkgutil.walk_packages(quince.__path__, "quince."):
            if info.name != "quince.__main__":
                importlib.import_module(info.name)
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QIcon, QImageReader
        from PySide6.QtMultimedia import QMediaPlayer, QMediaFormat
        from PySide6.QtNetwork import QLocalServer
        from PySide6.QtDBus import QDBusConnection
        from quince.integrations.install import icon_path
        app = QApplication([])
        formats = {bytes(f).decode() for f in QImageReader.supportedImageFormats()}
        missing = {"svg", "jpeg", "png", "gif", "webp", "tiff"} - formats
        assert not missing, f"image formats missing: {missing}"
        assert not QIcon(str(icon_path())).pixmap(48, 48).isNull(), "SVG icon"
        player = QMediaPlayer()
        assert QMediaFormat().supportedFileFormats(QMediaFormat.ConversionMode.Decode), "ffmpeg"
        print("smoke test ok:", len(sys.modules), "modules")
        """
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(venv.parent),
        "QT_QPA_PLATFORM": "offscreen",
        "XDG_RUNTIME_DIR": str(venv.parent),
    }
    run(venv / "bin" / "python3", "-I", "-c", code, env=env)


def md5sums(stage: Path) -> str:
    lines = []
    for path in walk_files(stage):
        rel = path.relative_to(stage)
        if rel.parts[0] == "DEBIAN" or path.is_symlink():
            continue
        lines.append(f"{hashlib.md5(path.read_bytes()).hexdigest()}  {rel.as_posix()}")
    return "\n".join(lines) + "\n"


def installed_size_kb(stage: Path) -> int:
    total = sum(p.lstat().st_size for p in walk_files(stage) if "DEBIAN" not in p.parts)
    return (total + 1023) // 1024


def build(keep: bool = False) -> Path:
    version = project_version()
    arch = output("dpkg", "--print-architecture").strip()
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    stage, work = BUILD_DIR / "root", BUILD_DIR / "work"
    venv = stage / PREFIX.relative_to("/")
    work.mkdir(parents=True)

    make_venv(venv, work)
    prune_pyside(site_packages(venv) / "PySide6")
    smoke_test(venv)
    desktop_files(stage)
    write(stage / "usr/share/doc" / PACKAGE / "copyright", notice(venv))
    compile_python(stage, venv)
    normalize_modes(stage)

    packages, unresolved = system_depends(stage)
    if unresolved:
        print("warning: no package found for", ", ".join(sorted(unresolved)))
    debian = stage / "DEBIAN"
    write(
        debian / "control",
        control_file(version, arch, installed_size_kb(stage), [*DEPENDS, *packages]),
    )
    write(debian / "conffiles", f"/etc/xdg/autostart/{PACKAGE}.desktop\n")
    write(debian / "prerm", (ROOT / "packaging/deb/prerm").read_text(), 0o755)
    write(debian / "md5sums", md5sums(stage))

    DIST_DIR.mkdir(exist_ok=True)
    deb = DIST_DIR / f"{PACKAGE}_{version}_{arch}.deb"
    run("dpkg-deb", "--root-owner-group", "--build", stage, deb)
    if not keep:
        shutil.rmtree(BUILD_DIR)
    size = deb.stat().st_size / 1e6
    print(f"\nbuilt {deb.relative_to(ROOT)} ({size:.0f} MB)")
    print(f"install: sudo apt install ./{deb.relative_to(ROOT)}")
    return deb


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true", help="keep packaging/deb/build/")
    args = parser.parse_args(argv)
    build(keep=args.keep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
