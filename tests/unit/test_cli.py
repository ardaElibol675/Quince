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


def test_render_doctor_terminal():
    from quince.backends.desktop import Terminal

    found = cli.render_doctor(_statuses(True, True), terminal=Terminal("/usr/bin/xterm", "xterm"))
    assert "Terminal /usr/bin/xterm (xterm)" in found
    missing = cli.render_doctor(_statuses(True, True), terminal=None)
    assert "No terminal program found" in missing
    assert "Terminal" not in cli.render_doctor(_statuses(True, True))


def test_open_refuses_missing_paths(capsys, tmp_path):
    from quince import cli as quince_cli

    assert quince_cli.main(["open", str(tmp_path / "missing.png")]) == 2
    assert "no such file" in capsys.readouterr().err


def test_open_sends_absolute_paths_to_the_service(monkeypatch, tmp_path):
    import json

    from quince import app
    from quince import cli as quince_cli

    (tmp_path / "a b.png").write_bytes(b"x")
    sent = []
    monkeypatch.setattr(app, "ensure_service", lambda: False)
    monkeypatch.setattr(app, "send_command", lambda line: sent.append(line) or "ok")
    monkeypatch.chdir(tmp_path)
    assert quince_cli.main(["open", "--wheel", "actions", "a b.png"]) == 0
    command, _sep, arg = sent[0].partition(" ")
    assert command == "open"
    assert json.loads(arg) == {"paths": [str(tmp_path / "a b.png")], "wheel": "actions"}


def test_home_reports_a_service_that_cannot_start(monkeypatch, capsys):
    from quince import app
    from quince import cli as quince_cli

    def fail():
        raise app.ServiceUnavailable("the Quince service stopped at start (exit 2)")

    monkeypatch.setattr(app, "ensure_service", fail)
    assert quince_cli.main(["home"]) == 1
    assert "stopped at start" in capsys.readouterr().err
    monkeypatch.setattr(app, "ensure_service", lambda: False)
    monkeypatch.setattr(app, "send_command", lambda line: "error: nope")
    assert quince_cli.main(["library"]) == 1


def test_restart_stops_the_service_then_opens_home(monkeypatch, capsys):
    from quince import app
    from quince import cli as quince_cli

    calls = []
    monkeypatch.setattr(app, "stop_service", lambda: calls.append("stop") or True)
    monkeypatch.setattr(app, "ensure_service", lambda: calls.append("start") or True)
    monkeypatch.setattr(app, "send_command", lambda line: calls.append(line) or "ok")
    assert quince_cli.main(["service", "--restart"]) == 0
    assert calls == ["stop", "start", "home"]
    out = capsys.readouterr().out
    assert "Quince stopped." in out
    assert "Quince started." in out


def test_restart_reports_a_service_that_does_not_stop(monkeypatch, capsys):
    from quince import app
    from quince import cli as quince_cli

    def stuck():
        raise app.ServiceUnavailable("the Quince service did not stop")

    monkeypatch.setattr(app, "stop_service", stuck)
    monkeypatch.setattr(app, "ensure_service", lambda: pytest.fail("must not start"))
    assert quince_cli.main(["service", "--restart"]) == 1
    assert "did not stop" in capsys.readouterr().err


def test_stop_and_restart_are_exclusive(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["service", "--stop", "--restart"])
    assert exc.value.code == 2
