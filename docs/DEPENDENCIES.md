# Dependencies

Every external dependency of Quince, why it is needed, and what this machine can actually do.
`quince doctor` checks the same list at runtime (`src/quince/core/deps.py`).

- **Required**: Quince does not run without it.
- **Optional**: when it is missing, the operations that need it are hidden from the wheels and
  listed in Settings with the reason.

## System packages (apt)

| Package          | Needed for                                             | Required | Version (22.04) |
|------------------|--------------------------------------------------------|----------|-----------------|
| `python3-venv`   | creating the project virtualenv                        | yes      | 3.10.6          |
| `libxcb-cursor0` | Qt 6 `xcb` platform plugin (X11)                       | yes      | 0.1.1           |
| `ffmpeg`         | `ffmpeg` / `ffprobe`: audio and video conversion, tools | yes      | 4.4.2           |
| `libreoffice`    | `soffice`: documents, slides, spreadsheets             | no       | 7.3.7.2         |
| `pandoc`         | Markdown and EPUB routes                               | no       | 2.9.2.1         |
| `librsvg2-bin`   | `rsvg-convert`: SVG input                              | no       | 2.52.5          |
| `p7zip-full`     | `7z`: 7z archives (create and extract)                 | no       | 16.02           |
| `unrar`          | RAR input (multiverse, non-free)                       | no       | 6.1.5           |

Install everything on Ubuntu 22.04:

```bash
sudo apt install python3-venv libxcb-cursor0 ffmpeg libreoffice pandoc librsvg2-bin p7zip-full unrar
```

Expected in later phases (not needed yet): `python3-nautilus` (Phase 11, Nautilus extension);
`tesseract-ocr` and `tesseract-ocr-tur` (Phase 10, OCR for scanned PDFs); Python package
`pymupdf4llm` pinned to an exact version (Phase 10, PDF → Markdown).

Development only (optional, both installed on the dev machine): `xvfb` runs the nested-X
drag tests invisibly (`tests/integration/test_x11_drag.py`), and `python3-gi` with GTK 3
(part of Ubuntu desktop) runs their GTK3 drag source under the system Python. Without Xvfb
the tests need `xserver-xephyr` and `QUINCE_NESTED_X=1`, and they open a small window.

## Python packages (`.venv`, from `pyproject.toml`)

Exact versions are pinned in `requirements.lock` (install with `-c requirements.lock`);
`tests/unit/test_environment.py` fails when the `.venv` drifts from it. Quince removes a
foreign `PYTHONPATH` (ROS on the dev machine) from its own `sys.path` and from every child
process at startup; `QUINCE_KEEP_PYTHONPATH=1` turns this off.

| Package       | Needed for                                  | Version tested |
|---------------|---------------------------------------------|----------------|
| `PySide6`     | all UI (Qt 6)                               | 6.10.3         |
| `Pillow`      | image conversion and tools; native AVIF     | 12.3.0         |
| `pillow-heif` | HEIC/HEIF read and write                    | 1.8.1          |
| `pypdf`       | PDF merge, split, organize                  | 6.21.4         |
| `PyMuPDF`     | PDF rendering (page images) and text        | 1.28.2         |
| `pysubs2`     | subtitle conversion                         | 1.8.1          |
| `python-xlib` | global drag and trigger detection (XInput2, XFixes, X11) | 0.33   |
| `tomli`       | reading `config.toml` (stdlib `tomllib` from Python 3.11) | 2.5.0 |
| `tomli-w`     | writing `config.toml`                       | 1.2.0          |
| `ruff`        | dev: lint and format                        | 0.17.3         |
| `pytest`      | dev: tests                                  | 9.2.0          |
| `pytest-qt`   | dev: Qt widget tests                        | 4.5.0          |

PyMuPDF is AGPL-3.0. This is fine for personal use; check it before distributing a `.deb` (Phase 17).

## Verified format support

Probed on 2026-11-02 on the development machine (Ubuntu 22.04.5, GNOME, X11) with
`python scripts/check_formats.py`, which runs real tiny conversions on the generated fixtures.
Result: 48 ok, 0 failed, 12 skipped. The skipped probes need `pandoc`, `rsvg-convert`, `7z`
and `unrar`, which were not installed at the time. Run the script again after installing them.

### Images (Pillow + pillow-heif)
- Write and read back: PNG, JPEG, WebP, TIFF, BMP, GIF, HEIC, AVIF; PDF write. All ok.
- HEIC is encoded by the libheif/x265 build bundled in the `pillow-heif` wheel. AVIF uses
  Pillow's native plugin (Pillow ≥ 11.2). No apt package is needed for either.
- SVG input needs `rsvg-convert` (pending install).

### Audio and video (ffmpeg 4.4.2)
- Audio encoders ok: MP3 (`libmp3lame`), M4A (`aac`), WAV (`pcm_s16le`), FLAC, OGG
  (`libvorbis`), Opus (`libopus`), AIFF (`pcm_s16be`), WMA (`wmav2`).
- Video encoders ok: MP4/MOV/MKV (`libx264`), WebM (`libvpx-vp9` + `libopus`), AVI (`mpeg4`),
  WMV (`wmv2`). Also `libx265`, `libaom-av1` and `prores_ks` are available.
- Video → GIF (palettegen), video → MP3, GIF → MP4 and stream copy (MP4 → MKV) are all ok.
- Notes: no `libfdk_aac` (the built-in `aac` encoder is used). The native `opus`/`vorbis`
  encoders are experimental in ffmpeg 4.4, so the `lib*` encoders are used. There is no
  SVT-AV1, and `libaom-av1` is slow, so AV1 is not a default target.

### Documents, slides, spreadsheets (LibreOffice 7.3.7.2, headless, isolated profile)
- Writer: DOCX, ODT, DOC, RTF, HTML and TXT input are ok. Output to DOCX, ODT, PDF, TXT (UTF-8),
  XHTML, EPUB, PNG and JPG is ok.
- Impress: PPTX, PPT and ODP input are ok. Output to PDF, PPTX, ODP and PNG is ok.
- Calc: XLSX, XLS, ODS, CSV and TSV input are ok (CSV/TSV need an explicit `--infilter`).
  Output to PDF, XLSX, ODS, CSV and TSV is ok.
- PDF → DOCX via `writer_pdf_import` produces a file; this is the best-effort path for Phase 10.

### PDF, subtitles, archives
- PyMuPDF: page render at 300 DPI and text extraction are ok. pypdf: merge is ok.
- pysubs2: SRT ⇄ VTT is ok.
- zip / tar / tar.gz via the Python standard library are ok. 7z and RAR are pending install.

## Limitations and formats that cannot be fully supported

| Item | Limitation | Plan |
|------|------------|------|
| DOCX/PPTX → PNG/JPG | LibreOffice image export renders only the **first** page/slide | Multi-page image output goes through PDF, then PyMuPDF renders every page into a folder |
| XLSX/ODS → CSV/TSV | Exports only the first sheet by default | LibreOffice 7.2+ sheet token `-1` exports every sheet to its own file (verified). The output file name is `<file>-<sheet>.csv` |
| PDF → DOCX/ODT | Layout is imported as positioned text boxes, not reflowed text | Best effort, documented in `docs/FORMATS.md` |
| PDF → XLSX | No table extraction backend in the current stack | Needs a stack proposal in Phase 10 (or the route is dropped) |
| PDF → PPTX | Needs a PPTX writer for page images (not in the stack) | Needs a stack proposal in Phase 10 |
| Markdown → PDF | pandoc needs LaTeX for PDF output | Markdown → ODT/HTML with pandoc, then LibreOffice → PDF |
| RAR | Input only. `unrar` is non-free (multiverse); creating RAR needs non-free `rar` | No RAR output; no RAR test fixture is generated |
| AV1 video | Only `libaom-av1` (very slow) | Not offered as a default target |

## Environment notes

- **ROS on `PYTHONPATH`:** on this machine `~/.bashrc` sources ROS Humble. That puts
  `/opt/ros/humble/...` before the venv on `sys.path`, and ROS's pytest plugins break test
  collection. pytest is therefore configured with `--disable-plugin-autoload -p pytestqt.plugin`.
  No ROS package currently shadows a Quince dependency. The Phase 17 launcher should clear
  `PYTHONPATH`.
