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
| `pandoc`         | Markdown, HTML and EPUB routes; code highlighting      | no       | 2.9.2.1         |
| `librsvg2-bin`   | `rsvg-convert`: SVG input                              | no       | 2.52.5          |
| `p7zip-full`     | `7z`: 7z archives (create and extract)                 | no       | 16.02           |
| `unrar`          | RAR input (multiverse, non-free)                       | no       | 6.1.5           |
| `ghostscript`    | `gs`: PDF compression                                  | no       | 9.55.0          |
| `zbar-tools`     | `zbarimg`: reading QR codes and barcodes               | no       | 0.23.92         |
| `python3-nautilus` | "Open in Quince" in Nautilus                         | no       | 1.2.3           |

Install everything on Ubuntu 22.04:

```bash
sudo apt install python3-venv libxcb-cursor0 ffmpeg libreoffice pandoc librsvg2-bin p7zip-full unrar
```

**Desktop integration (file actions, Phase 6)** uses only what an Ubuntu desktop already has;
no package and no Python module was added. Nothing here is required: a missing piece only
affects one action, and the action explains it.

| Uses | From (22.04 package) | For |
|------|----------------------|-----|
| `applications/*.desktop`, `mimeapps.list`, `defaults.list` | apps themselves, `desktop-file-utils` | Open With: which apps open a type, the default app |
| `mime/globs2`, `mime/aliases`, `mime/subclasses` | `shared-mime-info` | file types for Open With, apps for parent types |
| `gsettings` (`org.gnome.desktop.default-applications.terminal`) | `libglib2.0-bin` | Open in Terminal: GNOME's terminal setting |
| `x-terminal-emulator` (Debian alternatives), else a known terminal | `gnome-terminal`, ... | Open in Terminal; `quince doctor` shows which one is used |
| `xdg-open` | `xdg-utils` | Float shelf: double-click opens an item |
| `org.freedesktop.FileManager1` (D-Bus) | `nautilus` | Show in Files (falls back to opening the folder) |

**Phase 10 (optional, not installed on the dev machine yet):** `tesseract-ocr` and
`tesseract-ocr-tur` give PyMuPDF's built-in OCR the English and Turkish models
(`/usr/share/tesseract-ocr/4.00/tessdata/{eng,tur}.traineddata`); without them a scanned PDF
page stops the conversion with that install hint. `p7zip-full` enables the 7Z routes and
`unrar` RAR input. `quince doctor` lists all four (`ocr-eng`, `ocr-tur`, `7z`, `unrar`).

```bash
sudo apt install tesseract-ocr tesseract-ocr-tur p7zip-full unrar
```

**Phase 11 (optional):** `python3-nautilus` (nautilus-python) loads the "Open in Quince"
menu extension that `quince integrate` installs into `~/.local/share/nautilus-python/extensions/`.
`quince doctor` reports it as `nautilus-python`. The Library uses Python's built-in `sqlite3`
and the tray uses Qt (shown by Ubuntu's AppIndicator extension, installed by default); no
Python package was added.

```bash
sudo apt install python3-nautilus && nautilus -q
```

**Phase 12 (optional):** `ghostscript` (`gs`, installed on Ubuntu desktops) compresses PDFs;
without it Compress is hidden for PDFs only. `zbar-tools` (`zbarimg`) reads QR codes and
barcodes; without it Read QR is hidden. Both are listed by `quince doctor` (`gs`, `zbarimg`).
No Python package was added.

```bash
sudo apt install zbar-tools
```

Development only (optional, both installed on the dev machine): `xvfb` runs the nested-X
drag tests invisibly (`tests/integration/test_x11_drag.py`), and `python3-gi` with GTK 3
(part of Ubuntu desktop) runs their GTK3 drag source under the system Python. Without Xvfb
the tests need `xserver-xephyr` and `QUINCE_NESTED_X=1`, and they open a small window.

## The `.deb` package (Phase 18)

The package (`scripts/build_deb.py`) depends on `python3.10`, `ffmpeg`, `util-linux`, `procps`
and the system libraries its bundled binaries link against (found automatically: Qt's xcb
platform, GTK 3 theme, PulseAudio, EGL/GL, fontconfig, ...; all part of an Ubuntu desktop). It
**recommends** `libreoffice-writer`, `libreoffice-calc`, `libreoffice-impress`, `pandoc`,
`librsvg2-bin`, `p7zip-full`, `ghostscript`, `zbar-tools`, `tesseract-ocr`,
`tesseract-ocr-tur`, `python3-nautilus` and `xdg-utils`, and **suggests** `unrar`.

Building it needs `python3.10-venv` (`python3-venv`), `dpkg-deb` (`dpkg`), `readelf`
(`binutils`) and `dpkg -S`; all are on the dev machine. pip downloads the locked Python
packages once if its cache does not have them (build time only).

## Python packages (`.venv`, from `pyproject.toml`)

Exact versions are pinned in `requirements.lock` (install with `-c requirements.lock`);
`tests/unit/test_environment.py` fails when the `.venv` drifts from it. Quince removes a
foreign `PYTHONPATH` (ROS on the dev machine) from its own `sys.path` and from every child
process at startup; `QUINCE_KEEP_PYTHONPATH=1` turns this off.

| Package       | Needed for                                  | Version tested |
|---------------|---------------------------------------------|----------------|
| `PySide6`     | all UI (Qt 6); QtMultimedia (bundled FFmpeg plugin) plays media in the editors | 6.10.3         |
| `Pillow`      | image conversion and tools; native AVIF     | 12.3.0         |
| `pillow-heif` | HEIC/HEIF read and write                    | 1.8.1          |
| `pypdf`       | PDF merge, split, organize                  | 6.21.4         |
| `PyMuPDF`     | PDF rendering (page images) and text        | 1.28.2         |
| `pysubs2`     | subtitle conversion                         | 1.8.1          |
| `pymupdf4llm` | PDF → Markdown (exact pin, no `pymupdf-layout` extra; AGPL-3.0 like PyMuPDF) | 0.3.4 |
| `tabulate`    | pulled in by pymupdf4llm                    | 0.10.0         |
| `python-xlib` | global drag and trigger detection (XInput2, XFixes, X11) | 0.33   |
| `tomli`       | reading `config.toml` (stdlib `tomllib` from Python 3.11) | 2.5.0 |
| `tomli-w`     | writing `config.toml`                       | 1.2.0          |
| `ruff`        | dev: lint and format                        | 0.17.3         |
| `pytest`      | dev: tests                                  | 9.2.0          |
| `pytest-qt`   | dev: Qt widget tests                        | 4.5.0          |

PyMuPDF and pymupdf4llm are AGPL-3.0. Quince has no license yet and the `.deb` is for personal
use; giving the package to others means following the AGPL for it as a whole. The package
lists every bundled package and its license in `/usr/share/doc/quince/copyright`.

## Verified format support

Probed on 2026-11-02 on the development machine (Ubuntu 22.04.5, GNOME, X11) with
`python scripts/check_formats.py`, which runs real tiny conversions on the generated fixtures.
Result: 48 ok, 0 failed, 12 skipped. The skipped probes need `pandoc`, `rsvg-convert`, `7z`
and `unrar`, which were not installed at the time. Run the script again after installing them.

### Images (Pillow + pillow-heif)
- Write and read back: PNG, JPEG, WebP, TIFF, BMP, GIF, HEIC, AVIF; PDF write. All ok.
- HEIC is encoded by the libheif/x265 build bundled in the `pillow-heif` wheel. AVIF uses
  Pillow's native plugin (Pillow ≥ 11.2). No apt package is needed for either.
- SVG input needs `rsvg-convert` (`librsvg2-bin`, pending install); without it the SVG
  routes are hidden and their tests skipped.
- Phase 7 measured a 12 MP photo (JPG source) into every target: 0.1–1.7 s each; HEIC is the
  slowest, PDF the fastest (the JPEG is embedded as it is).
- **Viewing the outputs:** Eye of GNOME decodes with gdk-pixbuf, which on 22.04 has no WebP,
  HEIC or AVIF loader by default. This does not affect Quince; to open those files in the
  image viewer install `sudo apt install webp-pixbuf-loader heif-gdk-pixbuf libavif-gdk-pixbuf`.
  `tests/integration/test_image_routes.py` checks every output with the gdk-pixbuf loaders
  that are installed.

### Audio and video (ffmpeg 4.4.2)
- Audio encoders ok: MP3 (`libmp3lame`), M4A (`aac`), WAV (`pcm_s16le`), FLAC, OGG
  (`libvorbis`), Opus (`libopus`), AIFF (`pcm_s16be`), WMA (`wmav2`).
- Video encoders ok: MP4/MOV/MKV (`libx264`), WebM (`libvpx-vp9` + `libopus`), AVI (`mpeg4`),
  WMV (`wmv2`). Also `libx265`, `libaom-av1` and `prores_ks` are available.
- Video → GIF (palettegen), video → MP3, GIF → MP4 and stream copy (MP4 → MKV) are all ok.
- Notes: no `libfdk_aac` (the built-in `aac` encoder is used). The native `opus`/`vorbis`
  encoders are experimental in ffmpeg 4.4, so the `lib*` encoders are used. There is no
  SVT-AV1, and `libaom-av1` is slow, so AV1 is not a default target.
- Phase 8 limits found by probing: `wmav2` fails above 48 kHz and above two channels (Quince
  passes `-ar 48000 -ac 2`); `libmp3lame` silently downmixes 5.1 (Quince says so on the card);
  Ogg cannot carry cover art in ffmpeg 4.4; M4A and FLAC can.
- Speed on the dev machine, 10 s 1080p H.264 clip: stream copy (MKV, MOV, M4A) 0.2 s, AVI
  0.8 s, GIF 1.1 s, WebM (VP9) 7.4 s.
- **Playing the outputs:** Videos (Totem) plays through GStreamer; 22.04 ships only
  `gstreamer1.0-plugins-good`, which cannot decode H.264, AAC, MPEG-4, WMV or GIF. This does not
  affect Quince (VLC plays everything); for Totem install
  `sudo apt install gstreamer1.0-libav`. `tests/integration/test_media_routes.py` checks every
  output with the GStreamer decoders that are installed.
- Phase 14 (media editors) findings: `showspectrum` is very slow and memory hungry at 1080p in
  ffmpeg 4.4, so the "Spectrum" picture uses `showcqt`; `-shortest` with a copied sound ends
  the MP4 before the first encoded picture, so Audio to video limits the length with `-t`;
  `aeval` (bleep) runs at ~250× real time; a stream-copied sound starts at an earlier packet
  than an exact seek, so exact cuts encode the sound too. Editor playback uses PySide6's own
  QtMultimedia FFmpeg plugin (no GStreamer needed).

### Documents, slides, spreadsheets (LibreOffice 7.3.7.2, headless, isolated profile)
- Writer: DOCX, ODT, DOC, RTF, HTML and TXT input are ok. Output to DOCX, ODT, PDF, TXT (UTF-8),
  XHTML, EPUB, PNG and JPG is ok.
- Impress: PPTX, PPT and ODP input are ok. Output to PDF, PPTX, ODP and PNG is ok.
- Calc: XLSX, XLS, ODS, CSV and TSV input are ok (CSV/TSV need an explicit `--infilter`).
  Output to PDF, XLSX, ODS, CSV and TSV is ok.
- PDF → DOCX via `writer_pdf_import` produces a file; this is the best-effort path for Phase 10.
- Phase 9: HTML input needs `--infilter="HTML (StarWriter)"` (otherwise it opens as a
  Writer/Web document that cannot export DOCX). CSV export with the "all sheets" option names
  every file `<name>-<Sheet>.csv`, also for a single sheet. CSV import turns `007` into `7`
  (spreadsheet behaviour), so CSV ⇄ TSV is done in Python instead.
- pandoc 2.9 cannot read RTF or DOC (LibreOffice converts them to DOCX first) and needs LaTeX
  for PDF (Markdown/EPUB → PDF go through ODT and LibreOffice). Its GFM writer separates lists
  with `<!-- end list -->` and writes sized images as HTML; Quince removes both.
- Known limits of DOCX/ODT → Markdown (pandoc 2.9): table column alignment is not read from
  DOCX; LibreOffice's "Quotations" paragraph style is not a quote in ODT; images saved by
  LibreOffice lose their description (a caption paragraph stays as text).
- **Offline:** LibreOffice and pandoc run inside `unshare -rn` (an unprivileged user and
  network namespace, util-linux; works on this machine: `kernel.unprivileged_userns_clone=1`),
  so a document's remote images are never fetched. Without namespaces, a dead proxy is set.
- Speed: one LibreOffice conversion takes about 1 s (the first one with a fresh Quince profile
  about 1.5 s); pandoc conversions take 0.1 s.

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
  No ROS package currently shadows a Quince dependency. The packaged launcher
  (`/usr/bin/quince`) runs Python with `-I` and unsets `PYTHONPATH`.
