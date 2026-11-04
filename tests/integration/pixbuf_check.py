"""Decode images with gdk-pixbuf, the library Eye of GNOME uses. Run with the system Python.

Prints the MIME types gdk-pixbuf can load, then one line per file:

    FORMATS image/png image/jpeg ...
    OK <path> <width>x<height>
    ERROR <path> <message>

Usage: /usr/bin/python3 pixbuf_check.py FILE...
"""

import sys

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

mimes = sorted({m for f in GdkPixbuf.Pixbuf.get_formats() for m in f.get_mime_types()})
print("FORMATS", " ".join(mimes), flush=True)
for path in sys.argv[1:]:
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        print(f"OK {path} {pixbuf.get_width()}x{pixbuf.get_height()}", flush=True)
    except GLib.Error as exc:
        print(f"ERROR {path} {exc.message}", flush=True)
