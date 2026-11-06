# Quince

Offline file converter and file toolbox for Linux (Ubuntu 22.04, GNOME, X11).

Start dragging files in the file manager, tap a trigger key, and a radial wheel opens at the
pointer. Drop the files on a slice to convert them, run a tool or a file action. Originals are
never modified; every result is a new file next to the original. Nothing ever goes over the
network.

| Wheel   | Trigger (tap during a drag) | Offers                                             |
|---------|-----------------------------|----------------------------------------------------|
| Convert | `Shift`                     | the formats the dragged files can become           |
| Tools   | `Ctrl`                      | Edit, Compress, Metadata, Merge, Extract, ...      |
| Actions | `Alt` (left)                | Copy, Move, Copy Path, Open With, Archive, ...     |

## Install

Quince is a `.deb` package for **Ubuntu 22.04** (amd64). It brings its own Python packages
(in `/opt/quince`) and uses the system's Python 3.10 and `ffmpeg`.

```bash
sudo apt install ./quince_0.1.0_amd64.deb
```

`apt` also installs the recommended tools (LibreOffice, pandoc, Ghostscript, Tesseract with
Turkish, ...). Each of them enables some operations; without one, only those operations are
hidden. To install without them, add `--no-install-recommends`. RAR input needs `unrar`
(multiverse): `sudo apt install unrar`.

Then **log out and in again** (the service starts at login), or start it now with `quince home`.
Restart Nautilus once for **Open in Quince** in its right-click menu: `nautilus -q`.

Quince needs an **X11 session**: choose "Ubuntu on Xorg" with the gear button on the login
screen. Wayland is not supported.

Check what this machine can do:

```bash
quince doctor
```

**Uninstall:** `sudo apt remove quince` stops every running Quince and removes it. Your
settings (`~/.config/quince`), Library (`~/.local/share/quince`) and the files Quince made stay.
`sudo apt purge quince` also removes the system-wide start-at-login entry.

## Usage

1. Start dragging one or more files in Nautilus.
2. While dragging, tap `Shift` (Convert), `Ctrl` (Tools) or `Alt` (Actions): press and release
   it without other keys. The wheel opens at the pointer. Holding a key, or using `Ctrl`/`Shift`
   for multi-selection, never opens it.
3. Choose with the mouse: release the drag on a slice. Releasing it on the center or outside
   the ring closes the wheel. Or choose with the keyboard: arrow keys, `Enter`.

Several files of one kind are handled together (a batch); different kinds and folders get the
Actions wheel only.

- **Convert**: images, sound, video, documents, slides, spreadsheets, PDF, subtitles, archives
  and source code (see [Formats](#formats)).
- **Tools**: **Compress** (by quality, a percentage or a target size), **Metadata** (view, and
  save a copy without it), **Extract** archives, **Join** videos, **Merge PDFs**, images **To
  One PDF**, **Read QR** codes; for several audio files or videos also **Remove Audio** and
  **Normalize** loudness; several images make a **Collage**.
- **Actions**: **Copy**, **Move** (to a folder you choose; recent destinations are in the
  dialog's sidebar), **Copy Path**, **Show in Files**, **Open With**, **Open in Terminal**,
  **Archive** (ZIP, TAR.GZ, 7Z) and **Float**, which puts the files on a small always-on-top
  shelf to drag them out later (dragging out always copies).

One image, video or sound file, or one or more PDFs, get an **Edit** slice on the Tools wheel,
which opens one editor for every change:

- **Image Editor**: Crop, Adjust (turn, mirror, resize, brightness, contrast, saturation),
  Redact (black box, blur, pixelate), Annotate (pen, highlighter, lines, arrows, boxes,
  ellipses, text) and Frame (padding, background, rounded corners, shadow).
- **Video Editor**: Trim, Crop, Redact (areas for a stretch of time), Speed, Sound (channels,
  bleeps, even loudness, remove the sound) and Split (**Save Parts…**); **Save Frames…** saves
  pictures of the marked frames.
- **Sound Editor**: Trim, Speed, Sound (channels, bleeps, even loudness) and Video (a
  waveform, bars, spectrum or picture video, saved as MP4).
- **PDF Editor**: every page in a grid. Organize (drag pages to a new place, turn, duplicate,
  delete, add blank pages, add the pages of other PDFs or pictures by dropping them in) and
  Split (**Save Parts…** at the selected pages, every N pages or by page ranges); **Save
  Selection…** saves the selected pages as a PDF of their own. Several PDFs open as one grid
  and are saved as one PDF. Text stays selectable and links keep working.

**Without a drag:** the **Quince window** (app menu, tray icon or `quince home`) has a drop
area, Open Files and Paste (files, a copied image or text); select files and press `Enter` or
tap a trigger key for a wheel. The **Library** lists every file Quince made. In Nautilus,
right-click → **Open in Quince** opens the wheel at the pointer. From a terminal:
`quince open [--wheel convert|tools|actions] FILE...`.

**Where results go:** next to the original, named after it: `photo.png` → `photo.jpg`,
`photo-compressed.jpg`, `photo-edited.png`. A taken name becomes `photo (1).jpg`; nothing is
ever overwritten. A failed or cancelled job leaves no file. Progress, results (**Show in
Files**) and errors appear as cards at the bottom right of the screen.

**Settings** (`Ctrl+,` in any Quince window, the tray menu or `quince settings`): trigger keys,
which slices each wheel shows and in what order (and which are unavailable, with the reason),
output folder and name suffixes, theme, wheel size, animations and start at login. Changes apply
at once.

## Keyboard shortcuts

**Wheel**

| Key | Does |
|-----|------|
| `Shift` / `Ctrl` / `Alt` tap | open the Convert / Tools / Actions wheel (during a drag); switch wheels (without a drag) |
| `←` `→` `↑` `↓`, `Home`, `End` | move the highlight |
| `Enter`, `Space` | choose the highlighted slice |
| `Esc`, `Tab`, right or middle click | close |

**Quince window**

| Key | Does |
|-----|------|
| `Ctrl+O` | open files |
| `Ctrl+V` | paste files, an image or text |
| `Enter` | a wheel for the selected files |
| `Ctrl+1` / `Ctrl+2` | Home / Library |
| `Ctrl+F` | search the Library |
| `Ctrl+,` | Settings |
| `Esc`, `Ctrl+W` | hide the window |

**Editors**

| Key | Does |
|-----|------|
| `Ctrl+1` … `Ctrl+9` | switch mode (Crop, Adjust, Redact, ...) |
| `Enter`, `Ctrl+S` | save (asks for the name, format and folder) |
| `Ctrl+Z` / `Ctrl+Shift+Z` | undo / redo (one history for every mode) |
| `\` | show the original (Crop: the preview) |
| `Esc` | close (asks first when there are changes) |

**Video and Sound Editor timeline**

| Key | Does |
|-----|------|
| `Space` / `Shift+Space` | play or pause / play from In to Out |
| `←` `→` | 1 s back / forward |
| `Shift+←` `Shift+→` | one frame (video) or 0.1 s (sound) |
| `I` / `O` | set In / Out |
| `M` | add a marker |
| `Home` / `End` | go to the start / end |

**PDF Editor**

| Key | Does |
|-----|------|
| `R` / `Shift+R` | turn right / left |
| `Ctrl+D` | duplicate the selected pages |
| `B` | add a blank page |
| `Delete`, `Backspace` | delete the selected pages |
| `Alt+←` `Alt+→` `Alt+↑` `Alt+↓` | move the selected pages |
| `Ctrl+O` | add pages from PDFs or pictures |

## Formats

68 formats and 409 conversions. The full table, with every route and what is left out on
purpose, is in [`docs/FORMATS.md`](docs/FORMATS.md).

| Kind | Formats |
|------|---------|
| Image | JPG, PNG, WebP, HEIC, TIFF, AVIF, BMP, GIF, SVG (input) |
| Sound | MP3, M4A, WAV, FLAC, OGG, Opus, AIFF, WMA |
| Video | MP4, MOV, MKV, WebM, AVI, WMV; video → GIF and sound |
| PDF | → DOCX, ODT, Markdown, TXT, EPUB, HTML, PPTX, XLSX (tables), PNG, JPG |
| Document | DOCX, DOC, ODT, RTF, Markdown, TXT, EPUB, HTML |
| Slides | PPTX, PPT, ODP |
| Spreadsheet | XLSX, XLS, ODS, CSV, TSV |
| Subtitle | SRT, VTT |
| Archive | ZIP, TAR, TAR.GZ, 7Z, RAR (input) |
| Code | Python, JavaScript, TypeScript, JSON, YAML, TOML, INI, Shell, C, C++, C#, Java, Kotlin, Go, Rust, PHP, Ruby, Lua, SQL, XML, CSS → Markdown, TXT, HTML, PDF |

## Limitations

- Quince needs an X11 session; **Wayland is not supported**.
- Only Nautilus has an Open in Quince menu entry; dragging works from any X11 file manager.
- PDF → DOCX/ODT/EPUB gives flowing, editable text; the page layout is not kept. PDF → XLSX
  finds ruled tables only. Scanned PDFs need `tesseract-ocr` (and `tesseract-ocr-tur`).
- DOC, RTF, PPT and XLS are input only. RAR is input only and needs `unrar`. SVG is input only.
- Animation is kept between GIF and WebP only; other targets get the first frame.
- Compress never keeps a bigger file, and says so when a target size cannot be reached.
- No AV1 target (only the very slow encoder is available on 22.04).
- The user interface is in English.

## Building the package

```bash
python scripts/build_deb.py          # → dist/quince_<version>_amd64.deb
```

Run it from the development virtualenv (below). It needs `python3.10-venv`, `dpkg-deb` and
`binutils`, and downloads the Python packages once unless pip's cache already has them. It
installs Quince and the exact versions of `requirements.lock` into a fresh virtualenv, removes
the Qt modules Quince does not use, checks that every Quince module still imports, and finds
the system packages the bundled libraries need. See the docstring of
[`scripts/build_deb.py`](scripts/build_deb.py) for the package layout.

## Development setup

1. Install the system packages listed in [`docs/DEPENDENCIES.md`](docs/DEPENDENCIES.md):

   ```bash
   sudo apt install python3-venv libxcb-cursor0 ffmpeg libreoffice pandoc librsvg2-bin p7zip-full unrar
   ```

   Only `python3-venv`, `libxcb-cursor0` and `ffmpeg` are required; the rest enable optional
   operations.

2. Create the virtualenv and install Quince in editable mode with dev tools:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -c requirements.lock -e '.[dev]'
   ```

   `requirements.lock` pins every Python package to the exact version Quince is tested with,
   so a new release upstream can never break a working setup. To upgrade on purpose:
   `pip install -U <package>`, run all tests, then `python scripts/lock_deps.py`.

3. Check the dependencies: `quince doctor`.

With the package installed as well, `quince` in a terminal is the packaged one; the development
copy is `.venv/bin/quince`. Do not run `quince integrate` then: the package already installs the
menu entry and the Nautilus extension (`quince doctor` warns about leftovers).

## Development commands

```bash
ruff check . && ruff format --check .          # lint
QT_QPA_PLATFORM=offscreen pytest               # tests
python scripts/make_fixtures.py                # generate sample files into tests/fixtures/generated/
python scripts/check_formats.py                # probe real format support on this machine
QUINCE_NESTED_X=1 pytest tests/integration/test_x11_drag.py   # drag tests in Xephyr (auto with Xvfb)
quince service -v                              # run the background service (drag + tap = wheel)
quince service --stop                          # stop it
quince service --restart                       # stop it, start it again and show Home
quince home | quince library                   # show the window (starts the service if needed)
quince open [--wheel convert|tools|actions] FILE...   # a wheel at the pointer for these files
quince integrate [--remove]                    # app menu entry + Nautilus "Open in Quince" (dev only)
quince service -v --demo-ops                   # with two demo jobs on the Tools wheel
python scripts/lock_deps.py [--check]          # update (or check) requirements.lock
quince wheel-demo --n 7                        # preview the wheel (Shift/Ctrl/Alt tap = switch, Q = quit)
quince list-ops FILE...                        # what each wheel offers for these files/folders
quince config                                  # print the effective settings (~/.config/quince/config.toml)
quince settings                                # open the Settings window (also Ctrl+, or the tray menu)
python scripts/gen_formats_table.py            # regenerate docs/FORMATS.md after changing formats or routes
python scripts/build_deb.py [--keep]           # build the .deb (--keep leaves packaging/deb/build/)
```

## License

No license has been chosen yet; Quince is for personal use. The `.deb` bundles third-party
Python packages under their own licenses, listed in `/usr/share/doc/quince/copyright`. PyMuPDF
and pymupdf4llm are AGPL-3.0, which matters before giving the package to anyone else.
