# Quince

Offline file converter and file toolbox for Linux (Ubuntu 22.04, GNOME, X11).

Start dragging files in the file manager, tap a trigger key, and a radial wheel opens at the
cursor. Drop the files on a slice to convert them, run a tool or a file action. Originals are
never modified; every result is a new file.

| Wheel   | Trigger (tap during a drag) |
|---------|-----------------------------|
| Convert | `Shift`                     |
| Tools   | `Ctrl`                      |
| Actions | `Alt`                       |

The Actions wheel offers **Copy**, **Move** (to a folder you choose; recent destinations
are in the dialog's sidebar), **Copy Path**, **Show in Files**, **Open With**, **Open in
Terminal** and **Float**, which puts the files on a small always-on-top shelf to drag them
out later (dragging out always copies).

The Tools wheel offers **Compress** (by quality, a percentage or a target size), **Metadata**
(view, and save a copy without it), **Remove Audio**, **Normalize** loudness, **Extract**
archives, **Join** videos, **Merge PDFs**, images **To One PDF** and **Read QR** codes; the
Actions wheel also has **Archive** (ZIP, TAR.GZ, 7Z).

Without a drag: the **Quince window** (app menu, tray icon or `quince home`) has a drop area,
Open Files (Ctrl+O) and Paste (Ctrl+V); select files and press Enter or tap a trigger key for
a wheel. The **Library** tab (Ctrl+F) lists every file Quince made. In Nautilus, right-click
→ **Open in Quince** opens the wheel at the pointer (`quince integrate` installs the menu entry
and the Nautilus extension; the extension needs `python3-nautilus`).

> Status: early development.

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

3. Check the dependencies:

   ```bash
   quince doctor
   ```

## Development commands

```bash
ruff check . && ruff format --check .          # lint
QT_QPA_PLATFORM=offscreen pytest               # tests
python scripts/make_fixtures.py                # generate sample files into tests/fixtures/generated/
python scripts/check_formats.py                # probe real format support on this machine
QUINCE_NESTED_X=1 pytest tests/integration/test_x11_drag.py   # drag tests in Xephyr (auto with Xvfb)
quince service -v                              # run the background service (drag + tap = wheel)
quince service --stop                          # stop it
quince home | quince library                   # show the window (starts the service if needed)
quince open [--wheel convert|tools|actions] FILE...   # a wheel at the pointer for these files
quince integrate [--remove]                    # app menu entry + Nautilus "Open in Quince"
quince service -v --demo-ops                   # with two demo jobs on the Tools wheel
python scripts/lock_deps.py [--check]          # update (or check) requirements.lock
quince wheel-demo --n 7                        # preview the wheel (Shift/Ctrl/Alt tap = switch, Q = quit)
quince list-ops FILE...                        # what each wheel offers for these files/folders
quince config                                  # print the effective settings (~/.config/quince/config.toml)
python scripts/gen_formats_table.py            # regenerate docs/FORMATS.md after changing formats or routes
```

## License

Not decided yet.
