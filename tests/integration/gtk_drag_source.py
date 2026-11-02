"""GTK3 stand-in for Nautilus 42 in nested-X tests. Run with the system Python (python3-gi).

Starts a real GTK3 XDND drag of the given files (button 1, Copy or Move offered), using the
same drag-source code as Nautilus, including its key and button handling during a drag.
Prints one line per event so the test can follow along:

    READY                    window is mapped
    DELETE REQUESTED         the drop target asked the source to delete the data (XDND DELETE)
    DRAG FAILED <reason>     the drag was cancelled or not accepted
    DRAG END <action>        the drag is over; <action> is what the target answered

Usage: /usr/bin/python3 gtk_drag_source.py FILE...
"""

import sys

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

URIS = [GLib.filename_to_uri(path, None) for path in sys.argv[1:]]


def say(text):
    print(text, flush=True)


def on_data_get(_widget, _context, selection, _info, _time):
    selection.set_uris(URIS)


def on_data_delete(_widget, _context):
    say("DELETE REQUESTED")


def on_drag_failed(_widget, _context, result):
    say(f"DRAG FAILED {result.value_nick}")
    return False


def on_drag_end(_widget, context):
    action = "+".join(context.get_selected_action().value_nicks) or "none"
    say(f"DRAG END {action}")
    GLib.timeout_add(1000, Gtk.main_quit)


window = Gtk.Window(title="quince-test-source")
window.set_default_size(300, 300)
window.move(0, 0)
area = Gtk.EventBox()
window.add(area)
area.drag_source_set(
    Gdk.ModifierType.BUTTON1_MASK,
    [Gtk.TargetEntry.new("text/uri-list", 0, 0)],
    Gdk.DragAction.COPY | Gdk.DragAction.MOVE,
)
area.connect("drag-data-get", on_data_get)
area.connect("drag-data-delete", on_data_delete)
area.connect("drag-failed", on_drag_failed)
area.connect("drag-end", on_drag_end)
window.connect("destroy", Gtk.main_quit)
window.connect("map-event", lambda *_: say("READY"))
window.show_all()
Gtk.main()
