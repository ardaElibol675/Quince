"""Subtitle conversions with pysubs2: SRT ⇄ VTT, and either to plain text.

Turkish subtitles are often Windows-1254, not UTF-8: the encoding is detected
(``backends/textfiles.py``) and the result is always UTF-8. Plain text is the spoken lines
in order, one cue per paragraph, without timings or formatting tags.
"""

from __future__ import annotations

from pathlib import Path

from quince.backends import textfiles
from quince.core.jobs import JobContext, JobError
from quince.core.routes import Route

FORMATS = {"srt": "srt", "vtt": "vtt"}


def convert(ctx: JobContext, src: Path, out: Path, route: Route) -> None:
    import pysubs2

    text, encoding = textfiles.read_text(src)
    try:
        subs = pysubs2.SSAFile.from_string(text, format_=FORMATS[route.source])
    except (pysubs2.exceptions.Pysubs2Error, ValueError) as exc:
        raise JobError(f"Not a readable {route.source.upper()} file: {exc}") from exc
    events = [e for e in subs.events if not e.is_comment]
    if not events:
        raise JobError("The file has no subtitles")
    if route.target == "txt":
        lines = [" ".join(e.plaintext.split()) for e in events]
        out.write_text("\n\n".join(line for line in lines if line) + "\n", encoding="utf-8")
    else:
        out.write_text(subs.to_string(FORMATS[route.target]), encoding="utf-8")
    if note := textfiles.encoding_note(encoding):
        ctx.note(note)
