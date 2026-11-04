"""Pure parts of the document backends: text decoding, CSV ⇄ TSV, LibreOffice options,
Markdown tidying and media gathering, profiles, the offline switch and doctor's self-tests."""

import threading
from pathlib import Path

import pytest

from quince import cli
from quince.backends import office, pandoc, process, table, textfiles
from quince.core.jobs import JobQueue, JobState
from quince.core.routes import Route
from quince.core.selection import Selection
from tests.unit.test_jobs import wait_finished

# --- text inputs ---------------------------------------------------------------------------


def test_decode_guesses_the_encoding():
    assert textfiles.decode("Ağaç".encode()) == ("Ağaç", "utf-8-sig")
    assert textfiles.decode("﻿Ağaç".encode()) == ("Ağaç", "utf-8-sig")  # BOM dropped
    assert textfiles.decode("Ağaç ışık".encode("cp1254")) == ("Ağaç ışık", "cp1254")
    assert textfiles.encoding_note("cp1254") == "Read as Windows-1254 (Turkish) text"
    assert textfiles.encoding_note("utf-8-sig") is None


def test_utf8_copy(tmp_path):
    src = tmp_path / "a.txt"
    src.write_bytes("Çay\r\nŞeker\r".encode("cp1254"))
    dest = tmp_path / "b.txt"
    assert textfiles.utf8_copy(src, dest) == "cp1254"
    assert dest.read_bytes() == "Çay\nŞeker\n".encode()


@pytest.mark.parametrize(
    ("text", "sep"),
    [
        ("a,b,c\n1,2,3\n", ","),
        ("a;b;c\n1,5;2;3\n", ";"),
        ("a\tb\n1\t2\n", "\t"),
        ("a|b\n1|2\n", "|"),
        ("single column\nvalue\n", ","),
    ],
)
def test_sniff_delimiter(text, sep):
    assert textfiles.sniff_delimiter(text) == sep


# --- CSV ⇄ TSV ------------------------------------------------------------------------------


def _table_job(src: Path, source: str, target: str):
    queue = JobQueue(workers=1)
    route = Route(source, target, "table")

    def run(ctx):
        table.convert(ctx, src, ctx.new_output(src, extension=target), route)

    try:
        return wait_finished(queue, queue.submit(run, Selection.from_paths([src]), title="t"))
    finally:
        queue.shutdown()


def test_csv_to_tsv_copies_fields_as_text(tmp_path):
    src = tmp_path / "a.csv"
    src.write_text('id,note\n007,"line one\nline two"\n1E5,"a ""quote"""\n', encoding="utf-8")
    snap = _table_job(src, "csv", "tsv")
    assert snap.state is JobState.SUCCEEDED and not snap.notes
    assert snap.outputs[0].read_text(encoding="utf-8") == (
        'id\tnote\n007\t"line one\nline two"\n1E5\t"a ""quote"""\n'
    )


def test_tab_inside_a_field_is_quoted(tmp_path):
    src = tmp_path / "a.csv"
    src.write_text("a,b\nx\ty,z\n", encoding="utf-8")
    out = _table_job(src, "csv", "tsv").outputs[0]
    assert out.read_text() == 'a\tb\n"x\ty"\tz\n'


# --- LibreOffice options -------------------------------------------------------------------


def test_infilters(tmp_path):
    comma = tmp_path / "c.csv"
    comma.write_text("a,b\n1.5,2\n", encoding="utf-8")
    semi = tmp_path / "s.csv"
    semi.write_text("a;b\n1,5;2\n", encoding="utf-8")
    tsv = tmp_path / "t.tsv"
    tsv.write_text("a\tb\n", encoding="utf-8")
    assert office.infilter_for("csv", comma) == "CSV:44,34,76,1,,1033"
    assert office.infilter_for("csv", semi) == "CSV:59,34,76,1,,1055"  # Turkish decimal comma
    assert office.infilter_for("tsv", tsv) == "CSV:9,34,76,1,,1033"
    assert office.infilter_for("html", comma) == "HTML (StarWriter)"
    assert office.infilter_for("txt", comma) == "Text (encoded):UTF8"
    assert office.infilter_for("docx", comma) is None


def test_sheet_names_from_libreoffice_files():
    assert office._sheet_name(Path("input-Sheet 1.csv"), "input") == "Sheet 1"
    assert office._sheet_name(Path("input-İkinci-Sayfa.csv"), "input") == "İkinci-Sayfa"


def test_libreoffice_cannot_write_everything():
    from quince.core.jobs import JobError

    with pytest.raises(JobError, match="cannot write PPTX"):
        office.convert_file(None, Path("/x/a.docx"), "docx", "pptx", Path("/nonexistent"))


def test_profiles_are_exclusive(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(office, "PROFILE_SLOTS", 2)
    held = []
    with office.profile() as first, office.profile() as second:
        held += [first, second]
        assert first != second
        result = []
        waiter = threading.Thread(target=lambda: result.append(_take()))
        waiter.start()
        waiter.join(0.5)
        assert waiter.is_alive(), "a third user waits while both profiles are in use"
    waiter.join(5)
    assert result and result[0] in held
    assert office.profiles_dir() == tmp_path / "quince" / "libreoffice"


def _take():
    with office.profile() as path:
        return path


# --- Markdown -----------------------------------------------------------------------------


def test_tidy_markdown():
    text = "- a\n- b\n\n<!-- end list -->\n\n1.  c\n\n\n\nend"
    assert pandoc.tidy_markdown(text) == "- a\n- b\n\n1.  c\n\nend\n"


def test_gather_media(tmp_path):
    result = tmp_path / "result"
    (result / "Pictures").mkdir(parents=True)
    (result / "media").mkdir()
    (result / "Pictures" / "a.png").write_bytes(b"a")
    (result / "media" / "b.png").write_bytes(b"b")
    (result / "abc123.png").write_bytes(b"c")
    md = result / "doc.md"
    md.write_text("![](./Pictures/a.png) ![](./media/b.png) ![x](./abc123.png)\n")
    assert pandoc.gather_media(result, md)
    assert md.read_text() == "![](media/a.png) ![](media/b.png) ![x](media/abc123.png)\n"
    assert sorted(p.name for p in result.iterdir()) == ["doc.md", "media"]
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "doc.md").write_text("text\n")
    assert not pandoc.gather_media(empty, empty / "doc.md")


def test_lua_filters_are_installed():
    for name in ("clean_markdown.lua", "offline_images.lua", "default_title.lua"):
        assert pandoc.lua(name).is_file()


# --- offline ------------------------------------------------------------------------------


def test_offline_env_points_at_a_dead_proxy():
    env = process.offline_env()
    assert env["https_proxy"] == env["HTTP_PROXY"] == process.DEAD_PROXY
    assert env["no_proxy"] == ""


def test_offline_prefix_is_a_namespace_or_nothing():
    assert process.offline_prefix() in ((), (process.shutil.which("unshare"), "-rn"))


# --- doctor -------------------------------------------------------------------------------


def test_doctor_lists_self_tests():
    from tests.unit.test_cli import _statuses

    report = cli.render_doctor(
        _statuses(True, True),
        self_tests=[("DOCX → Markdown", True, "all kept"), ("PDF → Markdown", None, "skipped")],
    )
    assert "Self-tests" in report
    assert "✓ DOCX → Markdown" in report and "- PDF → Markdown" in report


def test_self_test_skips_without_pandoc():
    from tests.unit.test_cli import _statuses

    tests = cli.run_self_tests(_statuses(True, False))
    assert tests == [("DOCX → Markdown", None, "skipped: pandoc is missing")]
