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
quince service -v --demo-ops                   # with two demo jobs on the Tools wheel
python scripts/lock_deps.py [--check]          # update (or check) requirements.lock
quince wheel-demo --n 7                        # preview the wheel (Shift/Ctrl/Alt tap = switch, Q = quit)
quince list-ops FILE...                        # what each wheel offers for these files/folders
quince config                                  # print the effective settings (~/.config/quince/config.toml)
python scripts/gen_formats_table.py            # regenerate docs/FORMATS.md after changing formats or routes
```

## License

Not decided yet.
