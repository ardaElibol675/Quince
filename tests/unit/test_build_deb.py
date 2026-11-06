"""scripts/build_deb.py: the pure parts of the .deb build and the files it installs."""

import configparser
import hashlib
import re
import shlex
import sys
from email.message import Message
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


@pytest.fixture(scope="module")
def build_deb():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import build_deb
    finally:
        sys.path.remove(str(SCRIPTS))
    return build_deb


def _control(text: str) -> dict[str, str]:
    fields, key = {}, None
    for line in text.splitlines():
        if line.startswith(" ") and key:
            fields[key] += "\n" + line
        else:
            key, _, value = line.partition(": ")
            fields[key] = value
    return fields


def test_version_comes_from_pyproject(build_deb):
    from quince import __version__

    assert build_deb.project_version() == __version__


def test_control_file(build_deb):
    text = build_deb.control_file("0.1.0", "amd64", 1234, ["libc6", "ffmpeg", "libc6"])
    fields = _control(text)
    assert fields["Package"] == "quince" and fields["Version"] == "0.1.0"
    assert fields["Architecture"] == "amd64" and fields["Installed-Size"] == "1234"
    assert fields["Depends"] == "ffmpeg, libc6"  # sorted, no duplicates
    assert "pandoc" in fields["Recommends"] and fields["Suggests"] == "unrar"
    assert "unrar" not in fields["Recommends"]  # non-free: suggested only
    summary, *body = fields["Description"].splitlines()
    assert len(summary) <= 80 and all(line.startswith(" ") for line in body)
    assert text.endswith("\n") and "\n\n" not in text


def test_python_and_ffmpeg_are_required(build_deb):
    assert {"python3.10", "ffmpeg"} <= set(build_deb.DEPENDS)
    assert build_deb.PYTHON == "/usr/bin/python3.10"


@pytest.mark.parametrize(
    ("folder", "name", "kept"),
    [
        ("platforms", "libqxcb.so", True),
        ("platforms", "libqwayland.so", False),
        ("platforms", "libqeglfs.so", False),
        ("imageformats", "libqsvg.so", True),
        ("imageformats", "libqpdf.so", False),  # would pull in QtPdf
        ("multimedia", "libffmpegmediaplugin.so", True),
        ("iconengines", "libqsvgicon.so", True),
        ("platforminputcontexts", "libibusplatforminputcontextplugin.so", True),
        ("platforminputcontexts", "libqtvirtualkeyboardplugin.so", False),
        ("sqldrivers", "libqsqlite.so", False),
        ("webview", "libqtwebview_webengine.so", False),
    ],
)
def test_qt_plugins_kept(build_deb, folder, name, kept):
    assert build_deb.keep_plugin(folder, name) is kept


@pytest.mark.parametrize(
    ("name", "kept"),
    [
        ("QtCore.abi3.so", True),
        ("QtMultimedia.abi3.so", True),
        ("QtWebEngineCore.abi3.so", False),
        ("Qt3DCore.abi3.so", False),
        ("libpyside6.abi3.so.6.10", True),
        ("libpyside6qml.abi3.so.6.10", False),
        ("__init__.py", True),
        ("QtCore.pyi", False),
        ("designer", False),
        ("qmlls", False),
    ],
)
def test_pyside_files_kept(build_deb, name, kept):
    assert build_deb.keep_pyside_file(name) is kept


def test_every_imported_qt_module_is_kept(build_deb):
    used = set()
    for path in (ROOT / "src" / "quince").rglob("*.py"):
        used |= set(re.findall(r"PySide6\.(Qt\w+)", path.read_text(encoding="utf-8")))
    assert used <= build_deb.PYSIDE_MODULES


def test_parse_dynamic(build_deb):
    out = """
Dynamic section at offset 0x1 contains 30 entries:
  Tag        Type                         Name/Value
 0x0000000000000001 (NEEDED)             Shared library: [libQt6Core.so.6]
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
 0x000000000000001d (RUNPATH)            Library runpath: [$ORIGIN:$ORIGIN/../lib]
 0x000000000000000e (SONAME)             Library soname: [libQt6Gui.so.6]
"""
    assert build_deb.parse_dynamic(out) == (
        ["libQt6Core.so.6", "libc.so.6"],
        ["$ORIGIN", "$ORIGIN/../lib"],
    )
    assert build_deb.parse_dynamic("") == ([], [])


def test_parse_ldconfig_takes_this_architecture(build_deb):
    out = """1234 libs found in cache `/etc/ld.so.cache'
	libz.so.1 (libc6,x86-64) => /lib/x86_64-linux-gnu/libz.so.1
	libz.so.1 (libc6) => /lib/i386-linux-gnu/libz.so.1
	libGL.so.1 (libc6,x86-64) => /lib/x86_64-linux-gnu/libGL.so.1
	libGL.so.1 (libc6,x86-64) => /usr/lib/other/libGL.so.1
"""
    assert build_deb.parse_ldconfig(out) == {
        "libz.so.1": "/lib/x86_64-linux-gnu/libz.so.1",
        "libGL.so.1": "/lib/x86_64-linux-gnu/libGL.so.1",
    }


def test_parse_dpkg_search(build_deb):
    out = (
        "zlib1g:amd64: /lib/x86_64-linux-gnu/libz.so.1\n"
        "libgl1:amd64, libgl1-mesa:amd64: /usr/lib/x86_64-linux-gnu/libGL.so.1\n"
        "diversion by foo from: /usr/lib/x\n"
    )
    assert build_deb.parse_dpkg_search(out) == {"zlib1g", "libgl1", "libgl1-mesa"}


def _dist(**fields):
    meta = Message()
    for key, value in fields.items():
        if isinstance(value, list):
            for item in value:
                meta[key.replace("_", "-")] = item
        else:
            meta[key.replace("_", "-")] = value
    return type("Dist", (), {"metadata": meta})()


def test_license_of(build_deb):
    assert build_deb.license_of(_dist(License_Expression="MIT", License="x")) == "MIT"
    assert build_deb.license_of(_dist(License="BSD-3-Clause")) == "BSD-3-Clause"
    long_text = "Permission is hereby granted\nto anyone"
    classifier = "License :: OSI Approved :: MIT License"
    assert build_deb.license_of(_dist(License=long_text, Classifier=[classifier])) == "MIT License"
    assert "license files" in build_deb.license_of(_dist())


def test_copyright_lists_bundled_packages(build_deb):
    text = build_deb.copyright_file([("pymupdf", "1.28", "AGPL-3.0"), ("Pillow", "12", "MIT")])
    assert text.startswith("Format: https://www.debian.org/doc/packaging-manuals/copyright")
    assert " Pillow 12: MIT\n pymupdf 1.28: AGPL-3.0" in text
    assert "personal use" in text and "AGPL" in text


def test_launcher_and_prerm_agree(build_deb):
    """prerm stops exactly the processes the launcher (and the service it starts) run."""
    launcher = (ROOT / "packaging/deb/quince").read_text()
    prerm = (ROOT / "packaging/deb/prerm").read_text()
    [exec_line] = [line for line in launcher.splitlines() if line.startswith("exec ")]
    argv = shlex.split(exec_line)[1:]
    assert argv[:4] == [f"{build_deb.PREFIX}/bin/python3", "-I", "-m", "quince"]
    assert argv[4:] == ["$@"]
    assert "unset PYTHONPATH PYTHONHOME" in launcher
    [pattern] = re.findall(r"^PATTERN='(.*)'$", prerm, re.MULTILINE)
    regex = re.compile(pattern)
    for args in (["service"], ["home"], ["open", "--", "/a b.png"], []):
        assert regex.search(" ".join([*argv[:4], *args]))
    assert not regex.search("/usr/bin/python3 -m quince service")
    assert not regex.search("/opt/quince/bin/python3 -I -m quincex")
    assert "remove|upgrade" in prerm and "pkill -KILL" in prerm


def test_installed_desktop_files(build_deb, qapp, tmp_path):
    build_deb.desktop_files(tmp_path)
    files = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    assert {
        "usr/bin/quince",
        "usr/share/applications/quince.desktop",
        "etc/xdg/autostart/quince.desktop",
        "usr/share/nautilus-python/extensions/quince_nautilus.py",
        "usr/share/icons/hicolor/scalable/apps/quince.svg",
        "usr/share/icons/hicolor/48x48/apps/quince.png",
        "usr/share/icons/hicolor/512x512/apps/quince.png",
        "usr/share/doc/quince/README.md",
    } <= files
    assert (tmp_path / "usr/bin/quince").stat().st_mode & 0o777 == 0o755

    def entry(rel):
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(tmp_path / rel)
        return parser["Desktop Entry"]

    menu = entry("usr/share/applications/quince.desktop")
    assert menu["Exec"] == "quince home" and menu["Icon"] == "quince"
    start = entry("etc/xdg/autostart/quince.desktop")
    assert start["Exec"] == "quince service" and start["TryExec"] == "quince"
    ext = (tmp_path / "usr/share/nautilus-python/extensions/quince_nautilus.py").read_text()
    assert ext.startswith("# Installed by the quince package.")
    assert "QUINCE_COMMAND = ['/usr/bin/quince']" in ext

    from PySide6.QtGui import QImage

    image = QImage(str(tmp_path / "usr/share/icons/hicolor/48x48/apps/quince.png"))
    assert (image.width(), image.height()) == (48, 48) and image.hasAlphaChannel()


def test_normalize_modes_and_md5sums(build_deb, tmp_path):
    (tmp_path / "bin").mkdir()
    script, data = tmp_path / "bin/run", tmp_path / "data.txt"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o775)
    data.write_text("hello")
    data.chmod(0o664)
    (tmp_path / "link").symlink_to("data.txt")
    (tmp_path / "DEBIAN").mkdir()
    (tmp_path / "DEBIAN/control").write_text("x")
    build_deb.normalize_modes(tmp_path)
    assert script.stat().st_mode & 0o777 == 0o755
    assert data.stat().st_mode & 0o777 == 0o644
    sums = set(build_deb.md5sums(tmp_path).splitlines())
    assert sums == {
        f"{hashlib.md5(b'hello').hexdigest()}  data.txt",
        f"{hashlib.md5(b'#!/bin/sh' + bytes([10])).hexdigest()}  bin/run",
    }  # no DEBIAN files, no symlinks
    assert build_deb.installed_size_kb(tmp_path) == 1  # 5 + 10 bytes, DEBIAN not counted
