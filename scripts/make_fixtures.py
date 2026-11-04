#!/usr/bin/env python3
"""Generate tiny sample files for every format category into tests/fixtures/generated/.

Nothing is downloaded. Files that need a missing tool are skipped with a reason.

Usage: python scripts/make_fixtures.py [--out DIR] [--force]
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "tests" / "fixtures" / "generated"
TIMEOUT_S = 120


class Skip(Exception):
    """Raised when a fixture cannot be generated on this machine."""


@dataclass
class Result:
    name: str
    ok: bool
    reason: str = ""


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"{cmd[0]} exited {proc.returncode}: {tail[0]}")


def need(binary: str) -> str:
    path = shutil.which(binary)
    if path is None:
        raise Skip(f"{binary} not installed")
    return path


# --- images -----------------------------------------------------------------


def _sample_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 48), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((4, 4, 40, 36), fill=(230, 120, 30, 255))
    draw.ellipse((28, 12, 60, 44), fill=(40, 120, 220, 200))
    return img


def make_image(fmt: str, out: Path) -> None:
    img = _sample_image()
    if fmt in {"JPEG", "BMP"}:
        img = img.convert("RGB")
    if fmt == "HEIF":
        import pillow_heif

        pillow_heif.register_heif_opener()
    img.save(out, format=fmt)


def make_animated_gif(out: Path) -> None:
    from PIL import Image

    frames = [Image.new("RGB", (32, 32), color) for color in ("red", "green", "blue")]
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=100, loop=0)


SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="64" height="48" viewBox="0 0 64 48">
  <rect x="4" y="4" width="36" height="32" fill="#e6781e"/>
  <circle cx="44" cy="28" r="16" fill="#2878dc" fill-opacity="0.8"/>
</svg>
"""


# --- audio / video ----------------------------------------------------------

AUDIO_CODECS: dict[str, list[str]] = {
    "wav": ["-c:a", "pcm_s16le"],
    "mp3": ["-c:a", "libmp3lame"],
    "m4a": ["-c:a", "aac"],
    "flac": ["-c:a", "flac"],
    "ogg": ["-c:a", "libvorbis"],
    "opus": ["-c:a", "libopus"],
    "aiff": ["-c:a", "pcm_s16be"],
    "wma": ["-c:a", "wmav2"],
}

VIDEO_CODECS: dict[str, list[str]] = {
    "mp4": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"],
    "mov": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"],
    "mkv": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "libvorbis"],
    "webm": ["-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", "-c:a", "libopus"],
    "avi": ["-c:v", "mpeg4", "-c:a", "libmp3lame"],
    "wmv": ["-c:v", "wmv2", "-c:a", "wmav2"],
}


def make_audio(ext: str, out: Path) -> None:
    ffmpeg = need("ffmpeg")
    run(
        [ffmpeg, "-hide_banner", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5"]
        + AUDIO_CODECS[ext]
        + [str(out)]
    )


def make_video(ext: str, out: Path) -> None:
    ffmpeg = need("ffmpeg")
    inputs = [
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
    ]  # fmt: skip
    run([ffmpeg, "-hide_banner", "-y", *inputs, "-shortest", *VIDEO_CODECS[ext], str(out)])


# --- pdf --------------------------------------------------------------------


def make_pdf(out: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    for number in (1, 2):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 96), f"Quince sample page {number}", fontsize=18)
        page.insert_text((72, 130), "The quick brown fox jumps over the lazy dog.")
    doc.save(out)
    doc.close()


# --- office documents (flat ODF sources converted with LibreOffice) ---------

_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
    'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0" '
    'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
    'office:version="1.2"'
)

FODT = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document {_NS} office:mimetype="application/vnd.oasis.opendocument.text">
 <office:body><office:text>
  <text:h text:outline-level="1">Quince sample</text:h>
  <text:p>Hello from Quince. This is a tiny test document.</text:p>
  <text:p>Second paragraph.</text:p>
 </office:text></office:body>
</office:document>
"""


def _cell(value: str | int) -> str:
    if isinstance(value, int):
        return (
            f'<table:table-cell office:value-type="float" office:value="{value}">'
            f"<text:p>{value}</text:p></table:table-cell>"
        )
    return (
        f'<table:table-cell office:value-type="string"><text:p>{value}</text:p></table:table-cell>'
    )


def _sheet(name: str, rows: list[list[str | int]]) -> str:
    body = "".join(
        "<table:table-row>" + "".join(_cell(v) for v in row) + "</table:table-row>" for row in rows
    )
    return f'<table:table table:name="{name}">{body}</table:table>'


FODS = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document {_NS} office:mimetype="application/vnd.oasis.opendocument.spreadsheet">
 <office:body><office:spreadsheet>
  {_sheet("Fruits", [["name", "count"], ["apple", 3], ["quince", 5]])}
  {_sheet("Totals", [["total", 8]])}
 </office:spreadsheet></office:body>
</office:document>
"""


def _slide(name: str, title: str) -> str:
    return (
        f'<draw:page draw:name="{name}">'
        '<draw:frame svg:x="2cm" svg:y="2cm" svg:width="22cm" svg:height="3cm">'
        f"<draw:text-box><text:p>{title}</text:p></draw:text-box></draw:frame></draw:page>"
    )


FODP = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document {_NS} office:mimetype="application/vnd.oasis.opendocument.presentation">
 <office:body><office:presentation>
  {_slide("Slide1", "Quince slide one")}
  {_slide("Slide2", "Quince slide two")}
 </office:presentation></office:body>
</office:document>
"""

# target extension -> (flat ODF source, LibreOffice --convert-to argument)
OFFICE_TARGETS: dict[str, tuple[str, str]] = {
    "odt": ("fodt", "odt"),
    "docx": ("fodt", "docx:MS Word 2007 XML"),
    "doc": ("fodt", "doc:MS Word 97"),
    "rtf": ("fodt", "rtf:Rich Text Format"),
    "ods": ("fods", "ods"),
    "xlsx": ("fods", "xlsx:Calc MS Excel 2007 XML"),
    "xls": ("fods", "xls:MS Excel 97"),
    "odp": ("fodp", "odp"),
    "pptx": ("fodp", "pptx:Impress MS PowerPoint 2007 XML"),
    "ppt": ("fodp", "ppt:MS PowerPoint 97"),
}
FLAT_SOURCES = {"fodt": FODT, "fods": FODS, "fodp": FODP}


def soffice_convert(src: Path, convert_to: str, outdir: Path) -> Path:
    """Convert with LibreOffice using a throwaway user profile; return the output path."""
    soffice = need("soffice")
    with tempfile.TemporaryDirectory(prefix="quince-lo-") as profile:
        run(
            [
                soffice,
                f"-env:UserInstallation=file://{profile}",
                "--headless",
                "--norestore",
                "--convert-to",
                convert_to,
                "--outdir",
                str(outdir),
                str(src),
            ]
        )
    produced = outdir / f"{src.stem}.{convert_to.split(':')[0]}"
    if not produced.exists():
        raise RuntimeError(f"LibreOffice produced no {produced.name}")
    return produced


def make_office(ext: str, out: Path) -> None:
    flat_ext, convert_to = OFFICE_TARGETS[ext]
    need("soffice")
    with tempfile.TemporaryDirectory(prefix="quince-fx-") as tmp:
        src = Path(tmp) / f"{out.stem}.{flat_ext}"
        src.write_text(FLAT_SOURCES[flat_ext], encoding="utf-8")
        produced = soffice_convert(src, convert_to, Path(tmp))
        shutil.move(str(produced), out)


# --- plain text formats -----------------------------------------------------

MARKDOWN = """# Quince sample

Hello from **Quince**. This is a tiny test document.

- one
- two
"""

HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Quince sample</title></head>
<body><h1>Quince sample</h1><p>Hello from <b>Quince</b>.</p></body></html>
"""

SRT = """1
00:00:00,000 --> 00:00:01,500
Hello from Quince.

2
00:00:01,600 --> 00:00:03,000
Second line.
"""

VTT = """WEBVTT

00:00:00.000 --> 00:00:01.500
Hello from Quince.

00:00:01.600 --> 00:00:03.000
Second line.
"""

TEXT_FILES: dict[str, str] = {
    "sample.txt": "Hello from Quince.\nSecond line.\n",
    "sample.md": MARKDOWN,
    "sample.html": HTML,
    "sample.csv": "name,count\napple,3\nquince,5\n",
    "sample.tsv": "name\tcount\napple\t3\nquince\t5\n",
    "sample.srt": SRT,
    "sample.vtt": VTT,
    "sample.svg": SVG,
}


def make_epub(out: Path) -> None:
    pandoc = need("pandoc")
    with tempfile.TemporaryDirectory(prefix="quince-fx-") as tmp:
        src = Path(tmp) / "sample.md"
        src.write_text(MARKDOWN, encoding="utf-8")
        run([pandoc, str(src), "--metadata", "title=Quince sample", "-o", str(out)])


# --- golden documents (Markdown output tests) --------------------------------

GOLDEN_SOURCE = ROOT / "tests" / "fixtures" / "golden" / "rich.md"


def _golden_workdir(tmp: Path) -> Path:
    """A folder with rich.md and the logo.png it shows."""
    from PIL import Image, ImageDraw

    shutil.copy(GOLDEN_SOURCE, tmp / "rich.md")
    logo = Image.new("RGB", (48, 24), (240, 140, 40))
    ImageDraw.Draw(logo).rectangle((8, 6, 40, 18), fill=(40, 90, 200))
    logo.save(tmp / "logo.png")
    return tmp


def _golden_docx(tmp: Path) -> Path:
    """rich.md as a DOCX written by pandoc (proper Word numbering and styles)."""
    work = _golden_workdir(tmp)
    run([need("pandoc"), "-f", "markdown", "-t", "docx", f"--resource-path={work}",
         "-o", str(work / "rich.docx"), str(work / "rich.md")])  # fmt: skip
    return work / "rich.docx"


def make_golden(kind: str, out: Path) -> None:
    """The golden source in another format, the way users get such files.

    ``docx``: written by pandoc. ``lo.docx``, ``odt``, ``rtf``, ``doc``: the DOCX opened and
    saved by LibreOffice (``lo.docx`` goes through ODT first). ``html``, ``epub``: by pandoc.
    """
    with tempfile.TemporaryDirectory(prefix="quince-fx-") as tmp_name:
        tmp = Path(tmp_name)
        if kind in ("html", "epub"):
            work = _golden_workdir(tmp)
            extra = ["--standalone", "--self-contained"] if kind == "html" else []
            run([need("pandoc"), "-f", "markdown", "-t", "html5" if kind == "html" else "epub3",
                 *extra, f"--resource-path={work}", "--metadata", "title=Proje Raporu",
                 "-o", str(out),
                 str(work / "rich.md")])  # fmt: skip
            return
        docx = _golden_docx(tmp)
        if kind == "docx":
            shutil.move(str(docx), out)
            return
        odt = soffice_convert(docx, "odt", tmp / "odt")
        convert_to = {
            "odt": None,
            "lo.docx": "docx:MS Word 2007 XML",
            "rtf": "rtf:Rich Text Format",
            "doc": "doc:MS Word 97",
        }[kind]
        produced = odt if convert_to is None else soffice_convert(odt, convert_to, tmp / "final")
        shutil.move(str(produced), out)


GOLDEN_KINDS = ("docx", "lo.docx", "odt", "rtf", "doc", "html", "epub")

# --- golden PDFs (PDF → Markdown tests) ----------------------------------------

GOLDEN_PDF_HTML = ROOT / "tests" / "fixtures" / "golden" / "rich-pdf.html"
DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
DEJAVU_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
PDF_HEADER = "Çağrı Merkezi Raporu — Gizli"


def _font(path: Path) -> Path:
    if not path.exists():
        raise Skip(f"{path.name} missing (fonts-dejavu-core)")
    return path


def make_golden_pdf(out: Path) -> None:
    """rich-pdf.html as a real two-page PDF (LibreOffice), with a running header and a
    "Sayfa N / M" footer stamped on every page, as office documents have."""
    import pymupdf

    font = _font(DEJAVU)
    with tempfile.TemporaryDirectory(prefix="quince-fx-") as tmp_name:
        tmp = Path(tmp_name)
        src = tmp / "rich-pdf.html"
        shutil.copy(GOLDEN_PDF_HTML, src)
        produced = tmp / "out" / "rich-pdf.pdf"
        soffice = need("soffice")
        with tempfile.TemporaryDirectory(prefix="quince-lo-") as profile:
            run([soffice, f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
                 "--infilter=HTML (StarWriter)", "--convert-to", "pdf:writer_pdf_Export",
                 "--outdir", str(tmp / "out"), str(src)])  # fmt: skip
        doc = pymupdf.open(produced)
        for number, page in enumerate(doc, start=1):
            page.insert_text(
                (56, 16), PDF_HEADER, fontsize=7, fontname="dejavu", fontfile=str(font)
            )
            footer = f"Sayfa {number} / {doc.page_count}"
            page.insert_text((page.rect.width / 2 - 25, page.rect.height - 24), footer,
                             fontsize=8, fontname="dejavu", fontfile=str(font))  # fmt: skip
        doc.save(out)


COLUMN_LEFT = (
    "Sol sütun birinci paragraf: çağrılar sabah saatlerinde yoğunlaşır ve öğleden sonra azalır.",
    "Sol sütun ikinci paragraf: hafta sonu çağrıları hafta içine göre daha kısadır.",
)
COLUMN_RIGHT = (
    "Sağ sütun birinci paragraf: İzmir ve Muğla'da yaz aylarında artış görülür.",
    "Sağ sütun ikinci paragraf: İstanbul'da bekleme süresi en uzun olan şehirdir.",
)


def make_columns_pdf(out: Path) -> None:
    """A page with a full-width heading and two text columns: the reading order must be the
    left column, then the right one."""
    import pymupdf

    regular, bold = _font(DEJAVU), _font(DEJAVU_BOLD)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(
        (56, 80), "İki Sütunlu Bülten", fontsize=22, fontname="dvb", fontfile=str(bold)
    )
    for x, paragraphs in ((56, COLUMN_LEFT), (316, COLUMN_RIGHT)):
        text = "\n\n".join(paragraphs)
        rect = pymupdf.Rect(x, 110, x + 224, 400)
        page.insert_textbox(rect, text, fontsize=11, fontname="dv", fontfile=str(regular))
    doc.save(out)


def make_scanned_pdf(out: Path, golden_pdf: Path) -> None:
    """Page 1 of the golden PDF with its text layer, then the same page as a 200 DPI scan
    (an image only): the second page needs OCR."""
    import pymupdf

    if not golden_pdf.exists():
        make_golden_pdf(golden_pdf)
    src = pymupdf.open(golden_pdf)
    doc = pymupdf.open()
    doc.insert_pdf(src, from_page=0, to_page=0)
    pix = src[0].get_pixmap(dpi=200)
    page = doc.new_page(width=src[0].rect.width, height=src[0].rect.height)
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    doc.save(out)


# --- archives ---------------------------------------------------------------

ARCHIVE_MEMBERS = {"hello.txt": b"Hello from Quince.\n", "folder/inner.txt": b"inner\n"}


def make_zip(out: Path) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in ARCHIVE_MEMBERS.items():
            zf.writestr(name, data)


def make_tar(out: Path, mode: str) -> None:
    with tarfile.open(out, mode) as tf:
        for name, data in ARCHIVE_MEMBERS.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def make_7z(out: Path) -> None:
    seven = need("7z")
    with tempfile.TemporaryDirectory(prefix="quince-fx-") as tmp:
        for name, data in ARCHIVE_MEMBERS.items():
            path = Path(tmp) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        run([seven, "a", "-bd", "-y", str(out.resolve()), f"{tmp}/*"])


# --- registry ---------------------------------------------------------------


def fixtures() -> dict[str, Callable[[Path], None]]:
    """Map fixture file name -> generator."""
    table: dict[str, Callable[[Path], None]] = {}
    image_formats = {
        "png": "PNG", "jpg": "JPEG", "webp": "WEBP", "bmp": "BMP", "gif": "GIF",
        "tiff": "TIFF", "heic": "HEIF", "avif": "AVIF",
    }  # fmt: skip
    for ext, fmt in image_formats.items():
        table[f"sample.{ext}"] = lambda out, fmt=fmt: make_image(fmt, out)
    table["sample-animated.gif"] = make_animated_gif
    for ext in AUDIO_CODECS:
        table[f"sample-audio.{ext}"] = lambda out, ext=ext: make_audio(ext, out)
    for ext in VIDEO_CODECS:
        table[f"sample-video.{ext}"] = lambda out, ext=ext: make_video(ext, out)
    table["sample.pdf"] = make_pdf
    for ext in OFFICE_TARGETS:
        table[f"sample.{ext}"] = lambda out, ext=ext: make_office(ext, out)
    for name, content in TEXT_FILES.items():
        table[name] = lambda out, content=content: out.write_text(content, encoding="utf-8")
    table["sample.epub"] = make_epub
    table["sample.zip"] = make_zip
    table["sample.tar"] = lambda out: make_tar(out, "w")
    table["sample.tar.gz"] = lambda out: make_tar(out, "w:gz")
    table["sample.7z"] = make_7z
    for kind in GOLDEN_KINDS:
        table[f"golden/rich.{kind}"] = lambda out, kind=kind: make_golden(kind, out)
    table["golden/rich.pdf"] = make_golden_pdf
    table["golden/columns.pdf"] = make_columns_pdf
    table["golden/scanned.pdf"] = lambda out: make_scanned_pdf(out, out.parent / "rich.pdf")
    return table


def generate(out_dir: Path = DEFAULT_OUT, force: bool = False) -> list[Result]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []
    for name, make in fixtures().items():
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size > 0 and not force:
            results.append(Result(name, True, "exists"))
            continue
        target.unlink(missing_ok=True)
        try:
            make(target)
            if not target.exists() or target.stat().st_size == 0:
                raise RuntimeError("empty output")
            results.append(Result(name, True))
        except Skip as exc:
            results.append(Result(name, False, f"skipped: {exc}"))
        except Exception as exc:
            target.unlink(missing_ok=True)
            results.append(Result(name, False, f"failed: {exc}"))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--force", action="store_true", help="regenerate existing files")
    args = parser.parse_args(argv)

    results = generate(args.out, args.force)
    for r in results:
        print(f"{'✓' if r.ok else '✗'} {r.name:<24} {r.reason}")
    made = sum(r.ok for r in results)
    print(f"\n{made}/{len(results)} fixtures available in {args.out}")
    return 1 if any(r.reason.startswith("failed") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
