import threading
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from quince.core.forms import Choice, Form, Number, Shown, Text
from quince.core.operations import Choice as Action
from quince.core.operations import Report, ReportSection
from quince.ui.dialogs.options import FormDialog
from quince.ui.dialogs.order import OrderDialog
from quince.ui.dialogs.report import ReportDialog
from quince.ui.feedback.toast import Toast
from quince.ui.host import QtHost, run_off_gui_thread

FORM = Form(
    id="test.dialog",
    title="Compress",
    accept_label="Compress",
    note="A note",
    fields=(
        Choice("mode", "Goal", (("quality", "Quality"), ("size", "File size"))),
        Number("size", "At most", 0.1, 100, 2.0, decimals=1),
        Number("percent", "Percent", 5, 95, 50, suffix="%"),
        Text("name", "Name", default="Archive"),
    ),
    shown_when={"size": Shown("mode", ("size",))},
    remember=("mode", "size", "percent"),
)


def test_form_dialog_values_and_visibility(qtbot):
    dialog = FormDialog(FORM, FORM.defaults())
    qtbot.addWidget(dialog)
    assert dialog.values() == {"mode": "quality", "size": 2.0, "percent": 50, "name": "Archive"}
    assert not dialog.rows.isRowVisible(dialog.widgets["size"])
    dialog.set_value("mode", "size")
    assert dialog.rows.isRowVisible(dialog.widgets["size"])
    dialog.set_value("size", 7.5)
    dialog.set_value("percent", 200)  # clamped by the spin box
    dialog.set_value("name", "  Yedek ")
    assert dialog.values() == {"mode": "size", "size": 7.5, "percent": 95, "name": "Yedek"}


def test_order_dialog(qtbot):
    paths = [Path("/x/b.pdf"), Path("/x/page10.pdf"), Path("/x/page2.pdf")]
    dialog = OrderDialog("Merge", paths, accept_label="Merge")
    qtbot.addWidget(dialog)
    dialog.list.setCurrentRow(2)
    dialog.move(-1)
    assert [p.name for p in dialog.paths()] == ["b.pdf", "page2.pdf", "page10.pdf"]
    dialog.move(-5)  # no-op past the edge
    dialog.list.setCurrentRow(0)
    dialog.move(1)
    assert [p.name for p in dialog.paths()] == ["page2.pdf", "b.pdf", "page10.pdf"]
    dialog.sort_by_name()
    assert [p.name for p in dialog.paths()] == ["b.pdf", "page2.pdf", "page10.pdf"]
    dialog.list.setCurrentRow(0)
    dialog.remove_current()  # leave b.pdf out: greyed, still in the list
    assert [p.name for p in dialog.paths()] == ["page2.pdf", "page10.pdf"]
    assert [p.name for p in dialog.all_paths()] == ["b.pdf", "page2.pdf", "page10.pdf"]
    assert dialog.remove_button.text() == "Put Back"
    dialog.list.setCurrentRow(1)
    assert dialog.remove_button.text() == "Leave Out" and not dialog.remove_button.isEnabled()
    dialog.remove_current()  # at least two stay included
    assert len(dialog.paths()) == 2
    dialog.list.setCurrentRow(0)
    dialog.move(2)  # a left-out file can still be moved
    dialog.sort_by_name()  # and stays left out
    assert [p.name for p in dialog.left_out()] == ["b.pdf"]
    dialog.toggle(dialog.list.item(0))  # put back (double-click does the same)
    assert [p.name for p in dialog.paths()] == ["b.pdf", "page2.pdf", "page10.pdf"]
    assert dialog.buttons.button(dialog.buttons.StandardButton.Ok).isEnabled()


def test_report_dialog_copy_and_actions(qtbot):
    report = Report(
        "Metadata",
        (ReportSection("a.jpg", (("Make", "Cam"), ("GPS position", "41.0, 29.0"))),
         ReportSection("b.jpg", (), "No metadata")),
        actions=(Action("strip", "Save Copy Without Metadata"),),
        copy_all="all text",
        note="note",
    )  # fmt: skip
    dialog = ReportDialog(report)
    qtbot.addWidget(dialog)
    first = dialog.tree.topLevelItem(0)
    assert first.childCount() == 2 and first.child(1).text(1) == "41.0, 29.0"
    assert dialog.tree.topLevelItem(1).child(0).text(1) == "No metadata"
    first.child(0).setSelected(True)
    first.child(1).setSelected(True)
    dialog.copy_selected()
    assert QGuiApplication.clipboard().text() == "Cam\n41.0, 29.0"
    dialog.copy_all_button.click()
    assert QGuiApplication.clipboard().text() == "all text"
    dialog.action_buttons["strip"].click()
    assert dialog.chosen == "strip" and dialog.result() == dialog.DialogCode.Accepted


def test_host_remembers_options_and_runs_dialogs(qtbot, tmp_path):
    seen = {}

    def run_form(dialog):
        seen["initial"] = dialog.values()
        dialog.set_value("mode", "size")
        dialog.set_value("size", 3.0)
        return dialog.values()

    toast = Toast()
    qtbot.addWidget(toast)
    options = tmp_path / "options.json"
    host = QtHost(toast, lambda: None, run_form=run_form, options_file=options,
                  run_order=lambda d: list(reversed(d.paths())),
                  run_report=lambda d: "strip")  # fmt: skip
    assert host.ask_options(FORM)["size"] == 3.0
    assert seen["initial"]["mode"] == "quality"
    host.ask_options(FORM)
    assert seen["initial"]["mode"] == "size" and seen["initial"]["size"] == 3.0
    assert host.ask_order("t", [Path("/a"), Path("/b")], accept_label="Go") == [
        Path("/b"), Path("/a")
    ]  # fmt: skip
    assert host.show_report(Report("r", ())) == "strip"


def test_compute_runs_off_the_gui_thread(qtbot):
    gui = threading.get_ident()
    assert run_off_gui_thread(lambda: threading.get_ident()) != gui
    slow = []
    result = run_off_gui_thread(lambda: __import__("time").sleep(0.8) or 42,
                                on_slow=lambda: slow.append(1))  # fmt: skip
    assert result == 42 and slow == [1]

    def boom():
        raise ValueError("bad file")

    with pytest.raises(ValueError, match="bad file"):
        run_off_gui_thread(boom)


def test_form_dialog_enter_accepts(qtbot):
    dialog = FormDialog(FORM, FORM.defaults())
    qtbot.addWidget(dialog)
    dialog.show()
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    assert dialog.result() == dialog.DialogCode.Accepted
