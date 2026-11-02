import pytest

from quince import __version__, cli
from quince.core import deps
from quince.core.deps import Dependency, DependencyStatus, DepKind


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"quince {__version__}"


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 0
    assert "doctor" in capsys.readouterr().out


def _statuses(ffmpeg_ok: bool, pandoc_ok: bool) -> list[DependencyStatus]:
    ffmpeg = Dependency("ffmpeg", DepKind.BINARY, "ffmpeg", True, "audio and video", apt="ffmpeg")
    pandoc = Dependency("pandoc", DepKind.BINARY, "pandoc", False, "Markdown", apt="pandoc")
    return [
        DependencyStatus(ffmpeg, ffmpeg_ok, "4.4.2" if ffmpeg_ok else None),
        DependencyStatus(pandoc, pandoc_ok, "2.9" if pandoc_ok else None),
    ]


def test_render_doctor_all_present():
    report = cli.render_doctor(_statuses(True, True))
    assert "all required dependencies found" in report
    assert "sudo apt install" not in report


def test_render_doctor_lists_missing_with_apt_hint():
    report = cli.render_doctor(_statuses(True, False))
    assert "✗ pandoc" in report
    assert "(apt: pandoc)" in report
    assert "operations that need them are hidden" in report
    assert report.rstrip().endswith("sudo apt install pandoc")


@pytest.mark.parametrize(("ffmpeg_ok", "code"), [(True, 0), (False, 1)])
def test_doctor_exit_code_depends_on_required(monkeypatch, capsys, ffmpeg_ok, code):
    monkeypatch.setattr(deps, "check_all", lambda: _statuses(ffmpeg_ok, False))
    assert cli.main(["doctor"]) == code
    assert "dependency report" in capsys.readouterr().out
