"""pandoc conversions: Markdown, HTML and EPUB, in and out of Word and OpenDocument.

**Markdown output** (the most important route): GitHub-flavored Markdown without raw HTML,
no line wrapping, and ``data/clean_markdown.lua`` to remove what Word and LibreOffice leave
behind (bookmark spans, indentation read as quotes, loose or split lists, missing table
headers). Images are extracted: a document without images gives one ``.md`` file; with
images, a folder holding ``name.md`` and ``media/``, linked relatively.

Other details:

- DOC and RTF (which pandoc 2.9 cannot read) are converted to DOCX by LibreOffice first.
- PDF output goes through ODT and LibreOffice (pandoc would need LaTeX for PDF).
- HTML output is one self-contained page (images embedded). EPUB gets the document's title,
  else the file name.
- pandoc runs without network access. Remote images cannot be embedded, so they become links
  (``data/offline_images.lua``) and the card says so; in Markdown they stay image links.
- Markdown and HTML inputs are read as UTF-8 (Windows-1254 files are converted first), and
  their relative images are found next to the source file.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from importlib import resources
from pathlib import Path

from quince.backends import office, process, textfiles
from quince.core import naming
from quince.core.jobs import JobContext, JobError
from quince.core.routes import Route

TIMEOUT_S = 300
READERS = {"docx": "docx", "odt": "odt", "epub": "epub", "html": "html", "md": "markdown"}
WRITERS = {
    "md": "gfm-raw_html",
    "docx": "docx",
    "odt": "odt",
    "html": "html5",
    "epub": "epub3",
    "txt": "plain",
}
VIA_DOCX = frozenset({"doc", "rtf"})  # read through LibreOffice
TEXT_SOURCES = frozenset({"md", "html"})
REMOTE_MARKER = "QUINCE:remote-images"
REMOTE_NOTE = "Remote images were kept as links (Quince works offline)"
_END_LIST = re.compile(r"\n<!-- end list -->\n")


def lua(name: str) -> Path:
    return Path(str(resources.files("quince.backends") / "data" / name))


def run_pandoc(
    ctx: JobContext | None,
    src: Path,
    reader: str,
    writer: str,
    out: Path,
    *,
    options: list[str] | None = None,
    cwd: Path | None = None,
    resource_dir: Path | None = None,
) -> process.ProcessResult:
    cmd = ["pandoc", "-f", reader, "-t", writer, *(options or [])]
    if resource_dir is not None:
        cmd += [f"--resource-path={resource_dir}"]
    cmd += ["-o", out, src]
    return process.run(cmd, ctx, timeout_s=TIMEOUT_S, cwd=cwd, offline=True)


def tidy_markdown(text: str) -> str:
    """What the gfm writer of pandoc 2.9 adds that the cleanup filter cannot remove: the
    ``<!-- end list -->`` comment between two lists (they are different kinds once the filter
    merged lists of the same kind, so Markdown keeps them apart anyway)."""
    text = _END_LIST.sub("\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def to_markdown(ctx: JobContext | None, src: Path, reader: str, workdir: Path, stem: str) -> Path:
    """Convert to Markdown inside ``workdir/result``; returns that folder, which holds
    ``stem.md`` and, if the document has images, ``media/``."""
    result = workdir / "result"
    result.mkdir()
    md = result / f"{stem}.md"
    run_pandoc(
        ctx,
        src.resolve(),
        reader,
        WRITERS["md"],
        md,
        options=["--wrap=none", f"--lua-filter={lua('clean_markdown.lua')}", "--extract-media=."],
        cwd=result,
        resource_dir=src.resolve().parent,
    )
    md.write_text(tidy_markdown(md.read_text(encoding="utf-8")), encoding="utf-8")
    return result


def _writer_options(target: str, stem: str) -> list[str]:
    options = [f"--lua-filter={lua('offline_images.lua')}"]
    if target == "html":
        options += ["--standalone", "--self-contained", "--metadata", f"pagetitle={stem}"]
    elif target == "epub":
        options += [
            f"--lua-filter={lua('default_title.lua')}",
            "--metadata",
            f"quince-title={stem}",
        ]
    elif target == "txt":
        options += ["--wrap=none"]
    return options


def _readable(ctx: JobContext, src: Path, source: str, workdir: Path) -> tuple[Path, str]:
    """A file pandoc can read, and its reader."""
    if source in VIA_DOCX:
        (docx,) = office.convert_file(ctx, src, source, "docx", workdir / "docx")
        return docx, "docx"
    if source in TEXT_SOURCES:
        plain = workdir / f"input.{source}"
        note = textfiles.encoding_note(textfiles.utf8_copy(src, plain))
        if note:
            ctx.note(note)
        return plain, READERS[source]
    return src, READERS[source]


def gather_media(result: Path, md: Path) -> bool:
    """Put every extracted image under ``media/`` and link it as ``media/...``.

    pandoc extracts DOCX and EPUB images to ``./media/``, ODT images to ``./Pictures/`` and
    images embedded in HTML (data URIs) to ``./<hash>.png``. Returns True when the document
    has images.
    """
    media = result / "media"
    text = md.read_text(encoding="utf-8")
    for item in sorted(p for p in result.rglob("*") if p.is_file() and p != md):
        rel = item.relative_to(result).as_posix()
        if not rel.startswith("media/"):
            media.mkdir(exist_ok=True)
            target = naming.unique_path(media / item.name)
            shutil.move(str(item), target)
            new = target.relative_to(result).as_posix()
            text = text.replace(f"](./{rel})", f"]({new})").replace(f"]({rel})", f"]({new})")
    text = text.replace("](./media/", "](media/")
    md.write_text(text, encoding="utf-8")
    for folder in sorted((p for p in result.rglob("*") if p.is_dir()), reverse=True):
        if folder != media and not any(folder.iterdir()):
            folder.rmdir()
    return media.is_dir() and any(p.is_file() for p in media.rglob("*"))


def _place_markdown(result: Path, stem: str, outputs) -> None:
    md = result / f"{stem}.md"
    if gather_media(result, md):
        folder = outputs.folder()
        for item in result.iterdir():
            shutil.move(str(item), folder / item.name)
    else:
        shutil.move(str(md), outputs.file())


def convert_into(ctx: JobContext, src: Path, route: Route, outputs) -> None:
    """Convert one file along ``route``; ``outputs`` allocates the result (file or folder)."""
    if route.source not in READERS and route.source not in VIA_DOCX:
        raise JobError(f"pandoc cannot read this file ({route})")
    with tempfile.TemporaryDirectory(prefix="quince-pandoc-") as tmp:
        workdir = Path(tmp)
        (workdir / "docx").mkdir()
        ctx.progress(None, f"Converting {src.name}")
        readable, reader = _readable(ctx, src, route.source, workdir)
        stem = src.stem
        if route.target == "md":
            _place_markdown(to_markdown(ctx, readable, reader, workdir, stem), stem, outputs)
            return
        resources_dir = src.resolve().parent
        if route.target == "pdf":
            odt = workdir / f"{stem}.odt"
            result = run_pandoc(
                ctx, readable, reader, "odt", odt,
                options=_writer_options("odt", stem), resource_dir=resources_dir,
            )  # fmt: skip
            (workdir / "pdf").mkdir()
            (pdf,) = office.convert_file(ctx, odt, "odt", "pdf", workdir / "pdf")
            shutil.move(str(pdf), outputs.file())
        else:
            out = outputs.file()
            result = run_pandoc(
                ctx, readable, reader, WRITERS[route.target], out,
                options=_writer_options(route.target, stem), resource_dir=resources_dir,
            )  # fmt: skip
        if any(REMOTE_MARKER in line for line in result.stderr_tail):
            ctx.note(REMOTE_NOTE)


SELF_TEST_SOURCE = """# Öz Test

**Güçlü** ve *eğik* söz.

- Bir
  - İki

| Ad | Şehir |
|----|-------|
| Ayşe | İzmir |
"""
SELF_TEST_EXPECT = ("# Öz Test", "**Güçlü**", "*eğik*", "İki", "| Ayşe", "İzmir")


def self_test() -> tuple[bool, str]:
    """A real DOCX → Markdown conversion for ``quince doctor``: (passed, explanation)."""
    with tempfile.TemporaryDirectory(prefix="quince-selftest-") as tmp:
        workdir = Path(tmp)
        source = workdir / "selftest.md"
        source.write_text(SELF_TEST_SOURCE, encoding="utf-8")
        docx = workdir / "selftest.docx"
        try:
            run_pandoc(None, source, "markdown", "docx", docx)
            result = to_markdown(None, docx, "docx", workdir, "selftest")
            text = (result / "selftest.md").read_text(encoding="utf-8")
        except JobError as exc:
            return False, exc.reason
        missing = [part for part in SELF_TEST_EXPECT if part not in text]
        if missing:
            return False, f"missing from the result: {', '.join(missing)}"
        if "<" in text:
            return False, "the result contains raw HTML"
    return True, "headings, emphasis, nested lists, tables and Turkish letters kept"
