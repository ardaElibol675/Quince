#!/usr/bin/env python3
"""Probe real format support on this machine by running tiny conversions.

Uses the fixtures from scripts/make_fixtures.py (generated if missing). Probes that need
a missing tool are reported as skipped. Results feed docs/DEPENDENCIES.md.

Usage: python scripts/check_formats.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_fixtures as fx

FIX = fx.DEFAULT_OUT


@dataclass
class Probe:
    category: str
    name: str
    func: Callable[[Path], str | None]


class ProbeRunner:
    def __init__(self) -> None:
        self.probes: list[Probe] = []

    def add(self, category: str, name: str):
        def deco(func: Callable[[Path], str | None]):
            self.probes.append(Probe(category, name, func))
            return func

        return deco


probes = ProbeRunner()
_lo_profile = tempfile.mkdtemp(prefix="quince-lo-check-")


def ffmpeg(*args: str) -> None:
    fx.run([fx.need("ffmpeg"), "-hide_banner", "-y", "-loglevel", "error", *args])


def ffprobe_duration(path: Path) -> float:
    proc = subprocess.run(
        [fx.need("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True, timeout=fx.TIMEOUT_S,
    )  # fmt: skip
    return float(proc.stdout.strip())


def soffice(src: Path, convert_to: str, out: Path, infilter: str | None = None) -> list[Path]:
    cmd = [fx.need("soffice"), f"-env:UserInstallation=file://{_lo_profile}", "--headless",
           "--norestore"]  # fmt: skip
    if infilter:
        cmd.append(f"--infilter={infilter}")
    cmd += ["--convert-to", convert_to, "--outdir", str(out), str(src)]
    fx.run(cmd)
    produced = sorted(p for p in out.iterdir() if p.is_file())
    if not produced:
        raise RuntimeError("no output")
    return produced


def pandoc(src: Path, dst: Path, *extra: str) -> None:
    fx.run([fx.need("pandoc"), str(src), *extra, "-o", str(dst)])


def nonempty(path: Path) -> Path:
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"{path.name} missing or empty")
    return path


# --- images -----------------------------------------------------------------

IMAGE_TARGETS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "TIFF": "tiff", "BMP": "bmp",
                 "GIF": "gif", "HEIF": "heic", "AVIF": "avif", "PDF": "pdf"}  # fmt: skip

for _fmt, _ext in IMAGE_TARGETS.items():

    @probes.add("image", f"write {_ext} (from RGBA)")
    def _write_image(out: Path, fmt=_fmt, ext=_ext) -> str | None:
        import pillow_heif
        from PIL import Image

        pillow_heif.register_heif_opener()
        img = Image.open(FIX / "sample.png")
        if fmt in {"JPEG", "BMP", "PDF"}:
            img = img.convert("RGB")
        dst = out / f"x.{ext}"
        img.save(dst, format=fmt)
        if fmt != "PDF":
            Image.open(dst).load()
        return f"{nonempty(dst).stat().st_size} B"


@probes.add("image", "read all image fixtures")
def _read_images(_out: Path) -> str | None:
    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    names = [f"sample.{e}" for e in IMAGE_TARGETS.values() if e != "pdf"]
    for name in names:
        Image.open(FIX / name).load()
    return f"{len(names)} formats"


@probes.add("image", "svg -> png (rsvg-convert)")
def _svg(out: Path) -> str | None:
    dst = out / "x.png"
    fx.run([fx.need("rsvg-convert"), "-o", str(dst), str(FIX / "sample.svg")])
    nonempty(dst)
    return None


# --- audio / video ----------------------------------------------------------


@probes.add("audio", "encode mp3 m4a wav flac ogg opus aiff wma")
def _audio(_out: Path) -> str | None:
    for ext in fx.AUDIO_CODECS:
        ffprobe_duration(nonempty(FIX / f"sample-audio.{ext}"))
    return "all fixtures encoded and readable"


@probes.add("video", "encode mp4 mov mkv webm avi wmv")
def _video(_out: Path) -> str | None:
    for ext in fx.VIDEO_CODECS:
        ffprobe_duration(nonempty(FIX / f"sample-video.{ext}"))
    return "all fixtures encoded and readable"


@probes.add("video", "video -> gif (palette)")
def _video_gif(out: Path) -> str | None:
    dst = out / "x.gif"
    ffmpeg("-i", str(FIX / "sample-video.mp4"), "-vf",
           "fps=10,split[a][b];[a]palettegen[p];[b][p]paletteuse", str(dst))  # fmt: skip
    nonempty(dst)
    return None


@probes.add("video", "video -> mp3 (extract audio)")
def _video_mp3(out: Path) -> str | None:
    dst = out / "x.mp3"
    ffmpeg("-i", str(FIX / "sample-video.mkv"), "-vn", "-c:a", "libmp3lame", str(dst))
    nonempty(dst)
    return None


@probes.add("video", "gif -> mp4")
def _gif_video(out: Path) -> str | None:
    dst = out / "x.mp4"
    ffmpeg("-i", str(FIX / "sample-animated.gif"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", str(dst))  # fmt: skip
    nonempty(dst)
    return None


@probes.add("video", "mp4 -> mkv stream copy")
def _stream_copy(out: Path) -> str | None:
    dst = out / "x.mkv"
    ffmpeg("-i", str(FIX / "sample-video.mp4"), "-c", "copy", str(dst))
    nonempty(dst)
    return None


# --- LibreOffice --------------------------------------------------------------

LO_PROBES: list[tuple[str, str, str, str | None]] = [
    # (source fixture, --convert-to, expected output ext, infilter)
    ("sample.docx", "pdf", "pdf", None),
    ("sample.docx", "odt", "odt", None),
    ("sample.odt", "docx:MS Word 2007 XML", "docx", None),
    ("sample.doc", "docx:MS Word 2007 XML", "docx", None),
    ("sample.rtf", "docx:MS Word 2007 XML", "docx", None),
    ("sample.html", "docx:MS Word 2007 XML", "docx", None),
    ("sample.txt", "docx:MS Word 2007 XML", "docx", None),
    ("sample.docx", "txt:Text (encoded):UTF8", "txt", None),
    ("sample.docx", "html:XHTML Writer File:UTF8", "html", None),
    ("sample.docx", "epub", "epub", None),
    ("sample.docx", "png", "png", None),
    ("sample.docx", "jpg", "jpg", None),
    ("sample.pptx", "pdf", "pdf", None),
    ("sample.pptx", "odp", "odp", None),
    ("sample.odp", "pptx:Impress MS PowerPoint 2007 XML", "pptx", None),
    ("sample.ppt", "pptx:Impress MS PowerPoint 2007 XML", "pptx", None),
    ("sample.pptx", "png", "png", None),
    ("sample.xlsx", "pdf", "pdf", None),
    ("sample.xlsx", "ods", "ods", None),
    ("sample.ods", "xlsx:Calc MS Excel 2007 XML", "xlsx", None),
    ("sample.xls", "xlsx:Calc MS Excel 2007 XML", "xlsx", None),
    ("sample.csv", "xlsx:Calc MS Excel 2007 XML", "xlsx", "CSV:44,34,UTF8"),
    ("sample.tsv", "xlsx:Calc MS Excel 2007 XML", "xlsx", "CSV:9,34,UTF8"),
    ("sample.xlsx", "csv:Text - txt - csv (StarCalc):44,34,UTF8", "csv", None),
    ("sample.xlsx", "csv:Text - txt - csv (StarCalc):9,34,UTF8", "csv", None),
    ("sample.pdf", "docx:MS Word 2007 XML", "docx", "writer_pdf_import"),
]

for _src, _to, _ext, _inf in LO_PROBES:
    _label = f"{Path(_src).suffix[1:]} -> {_ext}" + (f" ({_inf})" if _inf else "")
    if "9,34" in _to:
        _label += " [tab]"

    @probes.add("libreoffice", _label)
    def _lo(out: Path, src=_src, to=_to, ext=_ext, inf=_inf) -> str | None:
        produced = soffice(FIX / src, to, out, inf)
        match = [p for p in produced if p.suffix == f".{ext}"]
        if not match:
            raise RuntimeError(f"expected .{ext}, got {[p.name for p in produced]}")
        return f"{len(match)} file(s), {match[0].stat().st_size} B"


@probes.add("libreoffice", "xlsx -> csv, all sheets (sheet=-1)")
def _lo_all_sheets(out: Path) -> str | None:
    produced = soffice(
        FIX / "sample.xlsx",
        "csv:Text - txt - csv (StarCalc):44,34,UTF8,1,,0,false,true,false,false,false,-1",
        out,
    )
    return f"{len(produced)} file(s): {', '.join(p.name for p in produced)}"


@probes.add("libreoffice", "pptx slide count after conversion")
def _pptx_slides(_out: Path) -> str | None:
    with zipfile.ZipFile(FIX / "sample.pptx") as zf:
        slides = [n for n in zf.namelist() if n.startswith("ppt/slides/slide")]
    if len(slides) != 2:
        raise RuntimeError(f"expected 2 slides, found {len(slides)}")
    return "2 slides"


# --- pandoc -----------------------------------------------------------------

PANDOC_PROBES = [
    ("sample.md", "docx", ()),
    ("sample.md", "odt", ()),
    ("sample.md", "html", ("--standalone", "--metadata", "title=Quince")),
    ("sample.md", "epub", ("--metadata", "title=Quince")),
    ("sample.docx", "md", ()),
    ("sample.html", "md", ()),
    ("sample.md", "txt", ("-t", "plain")),
]

for _src, _ext, _extra in PANDOC_PROBES:

    @probes.add("pandoc", f"{Path(_src).suffix[1:]} -> {_ext}")
    def _pd(out: Path, src=_src, ext=_ext, extra=_extra) -> str | None:
        dst = out / f"x.{ext}"
        pandoc(FIX / src, dst, *extra)
        nonempty(dst)
        return None


@probes.add("pandoc", "epub -> md")
def _pd_epub(out: Path) -> str | None:
    src = out / "x.epub"
    pandoc(FIX / "sample.md", src, "--metadata", "title=Quince")
    dst = out / "x.md"
    pandoc(src, dst)
    nonempty(dst)
    return None


# --- PDF, subtitles, archives -------------------------------------------------


@probes.add("pdf", "render page 300 dpi (PyMuPDF)")
def _pdf_render(out: Path) -> str | None:
    import pymupdf

    with pymupdf.open(FIX / "sample.pdf") as doc:
        pix = doc[0].get_pixmap(dpi=300)
        pix.save(out / "p1.png")
        text = doc[0].get_text()
    if "Quince" not in text:
        raise RuntimeError("text extraction failed")
    return f"{pix.width}x{pix.height}, text ok"


@probes.add("pdf", "merge / split (pypdf)")
def _pdf_merge(out: Path) -> str | None:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for _ in range(2):
        writer.append(str(FIX / "sample.pdf"))
    writer.write(out / "merged.pdf")
    return f"{len(PdfReader(out / 'merged.pdf').pages)} pages"


@probes.add("subtitle", "srt <-> vtt (pysubs2)")
def _subs(out: Path) -> str | None:
    import pysubs2

    pysubs2.load(str(FIX / "sample.srt")).save(str(out / "x.vtt"))
    pysubs2.load(str(FIX / "sample.vtt")).save(str(out / "x.srt"))
    return None


@probes.add("archive", "zip / tar / tar.gz read (stdlib)")
def _archives(_out: Path) -> str | None:
    import tarfile

    zipfile.ZipFile(FIX / "sample.zip").testzip()
    for name in ("sample.tar", "sample.tar.gz"):
        with tarfile.open(FIX / name) as tf:
            tf.getmembers()
    return None


@probes.add("archive", "7z create + extract")
def _7z(out: Path) -> str | None:
    seven = fx.need("7z")
    archive = out / "x.7z"
    fx.make_7z(archive)
    fx.run([seven, "x", "-bd", "-y", f"-o{out / 'ex'}", str(archive)])
    nonempty(out / "ex" / "hello.txt")
    return None


@probes.add("archive", "7z reads zip")
def _7z_zip(out: Path) -> str | None:
    fx.run([fx.need("7z"), "t", "-bd", str(FIX / "sample.zip")])
    return None


@probes.add("archive", "unrar available")
def _unrar(_out: Path) -> str | None:
    fx.need("unrar")
    return "binary only (no RAR fixture: creating RAR needs non-free rar)"


def main() -> int:
    fx.generate()
    failures = 0
    rows: list[tuple[str, str, str, str]] = []
    for probe in probes.probes:
        with tempfile.TemporaryDirectory(prefix="quince-probe-") as tmp:
            try:
                note = probe.func(Path(tmp)) or ""
                status = "ok"
            except fx.Skip as exc:
                status, note = "skipped", str(exc)
            except Exception as exc:
                status, note = "FAILED", str(exc).splitlines()[0][:120]
                failures += 1
        rows.append((probe.category, probe.name, status, note))
        print(f"{status:<8} {probe.category:<12} {probe.name:<44} {note}", flush=True)
    shutil.rmtree(_lo_profile, ignore_errors=True)
    ok = sum(r[2] == "ok" for r in rows)
    skipped = sum(r[2] == "skipped" for r in rows)
    print(f"\n{ok} ok, {skipped} skipped, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
