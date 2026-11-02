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
   pip install -e '.[dev]'
   ```

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
```

## License

Not decided yet.
