import pytest

from quince.core import deps
from quince.core.deps import Dependency, DependencyStatus, DepKind


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ffmpeg version 4.4.2-0ubuntu0.22.04.1 Copyright (c)", "4.4.2"),
        ("LibreOffice 7.3.7.2 30(Build:2)", "7.3.7.2"),
        ("\n7-Zip [64] 16.02 : Copyright (c) 1999-2016", "16.02"),
        ("UNRAR 6.11 beta 1 freeware", "6.11"),
        ("pandoc 2.9.2.1\nCompiled with ...", "2.9.2.1"),
        ("no version here", None),
        ("", None),
    ],
)
def test_parse_version(text, expected):
    assert deps.parse_version(text) == expected


def test_missing_binary_is_unavailable():
    dep = Dependency("nothing", DepKind.BINARY, "quince-no-such-binary", False, "x", apt="pkg")
    status = deps.check(dep, which=lambda _name: None)
    assert not status.available
    assert status.version is None


def test_present_binary_reports_version(tmp_path):
    exe = tmp_path / "fake-tool"
    exe.write_text("#!/bin/sh\necho 'fake-tool 1.2.3'\n")
    exe.chmod(0o755)
    dep = Dependency("fake", DepKind.BINARY, "fake-tool", False, "x")
    status = deps.check(dep, which=lambda _name: str(exe))
    assert status.available
    assert status.version == "1.2.3"
    assert status.location == str(exe)


def test_python_module_checks():
    present = Dependency("pytest", DepKind.PYTHON, "pytest", True, "x", pip="pytest")
    absent = Dependency("nope", DepKind.PYTHON, "quince_no_such_module", True, "x")
    assert deps.check(present).available
    assert deps.check(present).version
    assert not deps.check(absent).available


def test_missing_apt_packages_deduplicates_and_skips_available():
    def status(dep_id, apt, available):
        return DependencyStatus(
            Dependency(dep_id, DepKind.BINARY, dep_id, False, "x", apt=apt), available
        )

    statuses = [
        status("ffmpeg", "ffmpeg", False),
        status("ffprobe", "ffmpeg", False),
        status("soffice", "libreoffice", True),
        status("7z", "p7zip-full", False),
    ]
    assert deps.missing_apt_packages(statuses) == ["ffmpeg", "p7zip-full"]


def test_declared_dependencies_are_installable():
    ids = [d.id for d in deps.DEPENDENCIES]
    assert len(ids) == len(set(ids)), "dependency ids must be unique"
    for dep in deps.DEPENDENCIES:
        source = dep.pip if dep.kind is DepKind.PYTHON else dep.apt
        assert source, f"{dep.id} has no install source"
        assert dep.enables
