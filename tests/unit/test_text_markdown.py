"""Markdown written by Quince from plain text, tables, code and slides."""

import shutil
import subprocess

import pytest

from quince.backends import markdown, slidetext, text
from quince.core.jobs import JobError

TRICKY_LINES = [
    "# not a heading",
    "#hashtag stays",
    "- not a list",
    "-5 °C stays",
    "+ plus",
    "---",
    "===",
    "1. not numbered",
    "2) neither",
    "a *b* _c_ `d` [e](f) <g> h|i ~~j~~",
    "AT&T &amp; &#169;",
    "> not a quote",
    "back\\slash",
    "Çağrı İşığı öğütür",
]


def test_escape_line_examples():
    assert markdown.escape_line("# a") == "\\# a"
    assert markdown.escape_line("#tag") == "#tag"
    assert markdown.escape_line("-5 C") == "-5 C"
    assert markdown.escape_line("---") == "\\---"
    assert markdown.escape_line("1. one") == "1\\. one"
    assert markdown.escape_line("a*b*") == "a\\*b\\*"
    assert markdown.escape_line("AT&T &amp;") == "AT&T \\&amp;"
    assert markdown.escape_line("    indented") == "indented"


@pytest.mark.skipif(not shutil.which("pandoc"), reason="needs pandoc")
def test_escaped_text_reads_back_literally():
    """pandoc reads Quince's Markdown back as exactly the original text."""
    md = markdown.text_paragraphs("\n\n".join(TRICKY_LINES))
    out = subprocess.run(
        ["pandoc", "-f", "gfm", "-t", "plain", "--wrap=none"],
        input=md,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert [p.strip() for p in out.strip().split("\n\n")] == TRICKY_LINES


def test_text_paragraphs_keep_line_breaks():
    source = "﻿First line\r\nsecond line\n\n\n  \nNew paragraph\n"
    assert markdown.text_paragraphs(source) == "First line\\\nsecond line\n\nNew paragraph"
    assert markdown.text_paragraphs("\n \n") == ""


def test_pipe_table():
    table = markdown.pipe_table([["Ad", "Not"], ["a|b", "çok\nsatır"], ["x"]])
    assert table.split("\n") == [
        "| Ad   | Not          |",
        "| ---- | ------------ |",
        "| a\\|b | çok<br>satır |",
        "| x    |              |",
    ]
    assert markdown.pipe_table([]) == ""


def test_fence_is_longer_than_any_backtick_run():
    assert markdown.fence("x = 1\n", "python") == "```python\nx = 1\n```"
    code = "a = '````'\n"
    assert markdown.fence(code, "python").startswith("`````python\n")
    assert markdown.fence("", "c") == "```c\n```"
    assert markdown.fence("a\r\nb", "") == "```\na\nb\n```"


def test_trim_and_sheet_markdown():
    assert text.trim([["a", "b", ""], ["", " "], ["c"], [""], []]) == [["a", "b"], [], ["c"]]
    md = text.sheets_markdown([("Fruits", [["name", "n"], ["quince", "5"]]), ("Empty", [])])
    assert md.startswith("| name")  # one sheet with data: no heading
    two = text.sheets_markdown([("A*", [["x"]]), ("B", [["y"]])])
    assert two.startswith("## A\\*\n\n| x")
    with pytest.raises(JobError, match="no data"):
        text.sheets_markdown([("A", [[""]])])


def _slide() -> slidetext.Slide:
    P = slidetext.Paragraph
    return slidetext.Slide(
        3,
        "",
        [
            P("Intro *text*"),
            P("deep first", level=3, bullet=True),
            P("top", level=1, bullet=True),
            P("child\nsecond line", level=2, bullet=True),
            slidetext.Table([["a", "b"], ["1"]]),
        ],
        ["Say hello", "Then # this"],
    )


def test_slide_markdown():
    assert text.slide_markdown(_slide()) == (
        "## Slide 3\n\n"
        "Intro \\*text\\*\n\n"
        "- deep first\n"
        "- top\n"
        "  - child\\\n"
        "    second line\n\n"
        "| a   | b   |\n| --- | --- |\n| 1   |     |\n\n"
        "> **Notes:** Say hello\n>\n> Then # this"
    )


def test_slide_text():
    assert text.slide_text(_slide()) == (
        "Slide 3\n=======\n\n"
        "Intro *text*\n\n"
        "- deep first\n- top\n  - child\n    second line\n\n"
        "a\tb\n1\n\n"
        "Notes: Say hello\n\nThen # this"
    )
