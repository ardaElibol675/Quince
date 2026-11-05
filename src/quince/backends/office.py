"""LibreOffice (``soffice --headless``) conversions: documents, slides and spreadsheets.

- Every call uses one of Quince's own LibreOffice profiles (``~/.cache/quince/libreoffice``),
  so conversions work while the user's LibreOffice is open, and a pool of them, so two jobs
  can convert at the same time. A profile is locked with ``flock`` while in use, also
  against other Quince processes.
- LibreOffice runs without network access (``process.run(offline=True)``): a document's
  remote images or links are never fetched.
- The input is given to LibreOffice under a plain name in a private folder, so odd file names
  cannot be misread as options. Text inputs (TXT, CSV, TSV) are first rewritten as UTF-8;
  CSV/TSV separators are detected, and semicolon files (Turkish Excel) are read with Turkish
  number rules (decimal comma).
- Page images (PNG/JPG of documents and slides) go through PDF and are rendered with
  PyMuPDF at 150 DPI: one image for a single page, a folder of numbered images otherwise.
- CSV/TSV from a workbook: one file per sheet; several sheets go into a folder.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

from quince.backends import process, textfiles
from quince.core import formats, paths
from quince.core.formats import Category
from quince.core.jobs import JobContext, JobError
from quince.core.routes import Route

log = logging.getLogger(__name__)

TIMEOUT_S = 600
PROFILE_SLOTS = 4
PAGE_DPI = 150
JPEG_QUALITY = 90

WRITER = {
    "docx": "docx:MS Word 2007 XML",
    "odt": "odt:writer8",
    "pdf": "pdf:writer_pdf_Export",
    "txt": "txt:Text (encoded):UTF8",
}
CALC = {
    "xlsx": "xlsx:Calc MS Excel 2007 XML",
    "ods": "ods:calc8",
    "pdf": "pdf:calc_pdf_Export",
    # separator, quote, UTF-8, first line, -, -, quote all text, -, as shown, -, -, all sheets
    "csv": "csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,true,false,false,-1",
    "tsv": "csv:Text - txt - csv (StarCalc):9,34,76,1,,0,false,true,true,false,false,-1",
}
IMPRESS = {
    "pptx": "pptx:Impress MS PowerPoint 2007 XML",
    "odp": "odp:impress8",
    "pdf": "pdf:impress_pdf_Export",
}
FILTERS = {Category.DOCUMENT: WRITER, Category.SPREADSHEET: CALC, Category.SLIDES: IMPRESS}
TEXT_INPUTS = frozenset({"txt", "csv", "tsv"})  # HTML declares its own charset
LANG_EN_US, LANG_TR = 1033, 1055


# --- profiles -------------------------------------------------------------------------


def profiles_dir() -> Path:
    return paths.cache_dir() / "libreoffice"


@contextlib.contextmanager
def profile(ctx: JobContext | None = None) -> Iterator[Path]:
    """A free Quince LibreOffice profile folder, locked while the block runs."""
    root = profiles_dir()
    root.mkdir(parents=True, exist_ok=True)
    handles = [open(root / f"profile-{i}.lock", "a+") for i in range(PROFILE_SLOTS)]  # noqa: SIM115
    try:
        while True:
            for i, handle in enumerate(handles):
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                try:
                    yield root / f"profile-{i}"
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
                return
            if ctx is not None:
                ctx.check()
            time.sleep(0.2)  # every profile busy: wait for one
    finally:
        for handle in handles:
            handle.close()


# --- running soffice ------------------------------------------------------------------


def _failure(result: process.ProcessResult) -> str:
    for line in reversed([*result.stdout_tail, *result.stderr_tail]):
        if "Error" in line or "error" in line:
            return line.strip()
    return "no output was written"


def soffice(
    ctx: JobContext | None,
    src: Path,
    convert_to: str,
    workdir: Path,
    *,
    infilter: str | None = None,
) -> list[Path]:
    """Run one conversion; returns the files LibreOffice wrote (sorted)."""
    outdir = workdir / "out"
    outdir.mkdir()
    with profile(ctx) as prof:
        cmd = [
            "soffice",
            f"-env:UserInstallation={prof.as_uri()}",
            "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nologo",
            *([f"--infilter={infilter}"] if infilter else []),
            "--convert-to", convert_to, "--outdir", outdir, src,
        ]  # fmt: skip
        result = process.run(cmd, ctx, timeout_s=TIMEOUT_S, offline=True, check=False)
    files = sorted(p for p in outdir.iterdir() if p.is_file())
    if not files:
        raise JobError(
            f"LibreOffice could not convert this file ({_failure(result)})",
            "\n".join([*result.stdout_tail, *result.stderr_tail]),
        )
    return files


def _plain_input(src: Path, format_id: str, workdir: Path, ctx: JobContext | None) -> Path:
    """The input under a safe name; text rewritten as UTF-8 (with a note if it was not)."""
    ext = formats.get(format_id).extension
    plain = workdir / f"input.{ext}"
    if format_id in TEXT_INPUTS:
        note = textfiles.encoding_note(textfiles.utf8_copy(src, plain))
        if note and ctx is not None:
            ctx.note(note)
    else:
        os.symlink(os.path.abspath(src), plain)
    return plain


def infilter_for(format_id: str, plain: Path) -> str | None:
    if format_id == "html":
        return "HTML (StarWriter)"
    if format_id == "txt":
        return "Text (encoded):UTF8"
    if format_id in ("csv", "tsv"):
        text = plain.read_text(encoding="utf-8")
        sep = "\t" if format_id == "tsv" else textfiles.sniff_delimiter(text)
        lang = LANG_TR if sep == ";" else LANG_EN_US
        return f"CSV:{ord(sep)},34,76,1,,{lang}"
    return None


def convert_file(
    ctx: JobContext | None, src: Path, source: str, target: str, workdir: Path
) -> list[Path]:
    """Convert ``src`` (format ``source``) to ``target`` inside ``workdir``."""
    family = FILTERS[formats.get(source).category]
    if target not in family:
        raise JobError(f"LibreOffice cannot write {formats.get(target).label} from this file")
    plain = _plain_input(src, source, workdir, ctx)
    return soffice(ctx, plain, family[target], workdir, infilter=infilter_for(source, plain))


# --- the backend entry point ------------------------------------------------------------


def render_pages(
    ctx: JobContext, pdf: Path, source: Path, image: str, outputs, *, dpi: int = PAGE_DPI
) -> None:
    """Every page of ``pdf`` as an image: one file, or a folder of numbered files."""
    import pymupdf

    with pymupdf.open(pdf) as doc:
        count = doc.page_count
        if count == 0:
            raise JobError("The document has no pages")
        if count == 1:
            targets = [outputs.file()]
        else:
            folder = outputs.folder()
            width = max(2, len(str(count)))
            ext = formats.get(image).extension
            targets = [folder / f"{source.stem}-{n:0{width}d}.{ext}" for n in range(1, count + 1)]
        for n, (page, target) in enumerate(zip(doc, targets, strict=True)):
            ctx.check()
            ctx.progress(0.5 + 0.5 * n / count, f"Page {n + 1} of {count}")
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            if image == "jpg":
                pix.save(str(target), output="jpeg", jpg_quality=JPEG_QUALITY)
            else:
                pix.save(str(target), output="png")


def sheet_name(produced: Path, stem: str) -> str:
    """``input-Sheet 1.csv`` → ``Sheet 1``."""
    name = produced.stem
    return name[len(stem) + 1 :] if name.startswith(f"{stem}-") else name


def _delimited(ctx: JobContext, files: list[Path], source: Path, target: str, outputs) -> None:
    ext = formats.get(target).extension
    if len(files) == 1:
        shutil.move(str(files[0]), outputs.file())
        return
    folder = outputs.folder()
    for produced in files:
        name = f"{source.stem}-{sheet_name(produced, 'input')}.{ext}"
        shutil.move(str(produced), folder / name.replace("/", "_"))
    ctx.note(f"{len(files)} sheets, one file each")


def convert_into(ctx: JobContext, src: Path, route: Route, outputs) -> None:
    """Convert one file along ``route``; ``outputs`` allocates the result (file or folder)."""
    with tempfile.TemporaryDirectory(prefix="quince-lo-") as tmp:
        workdir = Path(tmp)
        ctx.progress(None, f"Converting {src.name}")
        if route.target in ("png", "jpg"):
            (pdf,) = convert_file(ctx, src, route.source, "pdf", workdir)
            render_pages(ctx, pdf, src, route.target, outputs)
            return
        files = convert_file(ctx, src, route.source, route.target, workdir)
        if route.target in ("csv", "tsv"):
            _delimited(ctx, files, src, route.target, outputs)
            return
        shutil.move(str(files[0]), outputs.file())
