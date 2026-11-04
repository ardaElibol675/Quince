from pathlib import Path

from quince.core.jobs import JobSnapshot, JobState
from quince.integrations import files
from quince.ui.feedback.jobs import JobsPanel


def _snap(state=JobState.RUNNING, **kw):
    base = {"id": 1, "title": "Convert to JPG", "op_id": "convert.image.jpg",
            "sources": (Path("/x/photo.png"),), "state": state}  # fmt: skip
    return JobSnapshot(**{**base, **kw})


def _panel(qtbot, **kw):
    shown = []
    panel = JobsPanel(show_files=shown.append, **kw)
    qtbot.addWidget(panel)
    panel.shown = shown
    return panel


def test_running_card_shows_progress_and_cancels(qtbot):
    panel = _panel(qtbot)
    cancelled = []
    panel.cancel_requested.connect(cancelled.append)
    panel.on_event(_snap(JobState.QUEUED))
    panel.on_event(_snap(progress=0.4, message="Step 2 of 5"))
    card = panel.cards[1]
    assert panel.isVisible()
    assert card.bar.maximum() == 1000 and card.bar.value() == 400
    assert card.detail.text() == "Step 2 of 5"
    assert card.cancel_button.isVisibleTo(panel) and not card.files_button.isVisibleTo(panel)
    card.cancel_button.click()
    assert cancelled == [1]


def test_unknown_progress_is_a_busy_bar(qtbot):
    panel = _panel(qtbot)
    panel.on_event(_snap(progress=None))
    assert panel.cards[1].bar.maximum() == 0
    assert panel.cards[1].detail.text() == "photo.png"


def test_success_card_shows_files_and_disappears(qtbot):
    panel = _panel(qtbot, success_ms=50)
    out = Path("/x/photo.jpg")
    panel.on_event(_snap(JobState.SUCCEEDED, outputs=(out,), progress=1.0))
    card = panel.cards[1]
    assert card.detail.text() == "→ photo.jpg"
    assert card.files_button.isVisibleTo(panel) and not card.cancel_button.isVisibleTo(panel)
    card.files_button.click()
    assert panel.shown == [(out,)]
    qtbot.waitUntil(lambda: not panel.cards and not panel.isVisible(), timeout=2000)


def test_error_card_shows_reason_and_can_be_dismissed(qtbot):
    panel = _panel(qtbot)
    panel.on_event(_snap(JobState.FAILED, error="ffmpeg: Invalid data found"))
    card = panel.cards[1]
    assert card.detail.text() == "ffmpeg: Invalid data found"
    card.close_button.click()
    assert not panel.cards and not panel.isVisible()


def test_cards_stack_and_keep_the_corner(qtbot):
    panel = _panel(qtbot)
    panel.on_event(_snap(id=1))
    corner = panel.geometry().bottomRight()
    one_card = panel.height()
    panel.on_event(_snap(id=2, title="Merge PDFs"))
    assert len(panel.cards) == 2
    assert panel.height() > one_card * 1.5  # both cards fit
    assert panel.geometry().bottomRight() == corner
    panel.remove(1)
    assert panel.height() == one_card
    panel.on_event(_snap(JobState.CANCELLED, id=2))
    assert panel.cards[2].detail.text() == "Cancelled"


def test_many_sources_are_summarized(qtbot):
    panel = _panel(qtbot)
    sources = tuple(Path(f"/x/{n}.png") for n in "abcd")
    panel.on_event(_snap(sources=sources))
    assert panel.cards[1].detail.text() == "a.png, b.png +2 more"


def test_show_in_files_falls_back_without_dbus(monkeypatch, qtbot):
    class NoBus:
        def isConnected(self):
            return False

    monkeypatch.setattr(files.QDBusConnection, "sessionBus", staticmethod(lambda: NoBus()))
    opened = []
    files.show_in_files([Path("/x/out/photo.jpg")], fallback=opened.append)
    assert opened == [Path("/x/out")]
    files.show_in_files([], fallback=opened.append)
    assert len(opened) == 1


def test_success_card_shows_notes(qtbot):
    panel = _panel(qtbot)
    out = (Path("/x/photo.jpg"),)
    panel.on_event(
        _snap(JobState.SUCCEEDED, outputs=out, notes=("Transparent areas were filled with white",))
    )
    card = panel.cards[1]
    assert card.detail.text() == "→ photo.jpg\nTransparent areas were filled with white"
    assert "✓" in card.status.text()


def test_partial_success_card(qtbot):
    panel = _panel(qtbot, success_ms=10, error_ms=60_000)
    failures = tuple(f"bad{i}.png: not a readable PNG image" for i in range(5))
    panel.on_event(_snap(JobState.SUCCEEDED, outputs=(Path("/x/a.jpg"),), failures=failures))
    card = panel.cards[1]
    lines = card.detail.text().split("\n")
    assert lines[:2] == ["→ a.jpg", "5 files failed:"]
    assert lines[2:5] == list(failures[:3])
    assert lines[5] == "+2 more (see the log)"
    assert "!" in card.status.text()
    assert card.files_button.isVisibleTo(panel)
    qtbot.wait(100)
    assert 1 in panel.cards  # stays as long as an error card


def _fits(card) -> bool:
    label = card.detail
    return label.height() >= label.heightForWidth(label.width())


def test_card_grows_when_a_running_job_finishes_with_notes(qtbot):
    """Regression: the card kept its one-line running height and clipped the notes."""
    panel = _panel(qtbot)
    panel.on_event(_snap(JobState.QUEUED))
    panel.on_event(_snap(progress=0.0, message="Converting photo.png"))
    running = panel.cards[1].height()
    notes = ("1 file was already JPG", "Transparent areas were filled with white")
    panel.on_event(_snap(JobState.SUCCEEDED, outputs=(Path("/x/photo (1).jpg"),), notes=notes))
    card = panel.cards[1]
    assert _fits(card)
    assert card.height() > running
    assert panel.height() >= card.height()


def test_long_failure_lines_wrap_inside_the_card(qtbot):
    panel = _panel(qtbot)
    failures = tuple(f"bad{i}.png: {'a very long reason ' * 4}" for i in range(4))
    panel.on_event(_snap(progress=0.5))
    panel.on_event(_snap(JobState.SUCCEEDED, outputs=(Path("/x/a.jpg"),), failures=failures))
    assert _fits(panel.cards[1])
    panel.on_event(_snap(JobState.FAILED, error="x " * 200))
    assert _fits(panel.cards[1])
