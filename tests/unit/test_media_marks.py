"""The media timeline's marks and playhead rules."""

from __future__ import annotations

import pytest

from quince.ui.editors.media import marks as m
from quince.ui.editors.media.marks import Marks


def test_span_defaults_to_the_whole_file():
    assert Marks().span(10.0) == (0.0, 10.0)
    assert not Marks().has_range
    assert Marks(in_s=2.0).span(10.0) == (2.0, 10.0)
    assert Marks(out_s=4.0).span(10.0) == (0.0, 4.0)


def test_in_and_out_never_cross():
    marks = m.set_out(Marks(), 4.0, 10.0)
    marks = m.set_in(marks, 2.0)
    assert marks.span(10.0) == (2.0, 4.0)
    assert m.set_in(marks, 5.0) == Marks(in_s=5.0)  # an out before the new in is dropped
    assert m.set_out(Marks(in_s=5.0), 3.0, 10.0) == Marks(out_s=3.0)
    assert m.set_in(Marks(), 0.0) == Marks()  # in at the start is no mark
    assert m.set_out(Marks(), 10.0, 10.0) == Marks()  # out at the end is no mark
    assert m.clear_range(Marks(1.0, 2.0, (5.0,))) == Marks(markers=(5.0,))


def test_markers_toggle_and_stay_sorted():
    marks = m.toggle_marker(Marks(), 5.0, 0.1)
    marks = m.toggle_marker(marks, 2.0, 0.1)
    assert marks.markers == (2.0, 5.0)
    assert m.toggle_marker(marks, 5.05, 0.1).markers == (2.0,)  # removes the one there


def test_steps_and_clamp():
    assert m.fine_step(25.0, video=True) == pytest.approx(0.04)
    assert m.fine_step(0.0, video=True) == pytest.approx(1 / 30)
    assert m.fine_step(25.0, video=False) == 0.1
    assert m.clamp(-1.0, 5.0) == 0.0 and m.clamp(9.0, 5.0) == 5.0


def test_clock():
    assert m.clock(62.347) == "1:02.35"
    assert m.clock(59.999) == "1:00.00"
    assert m.clock(3723.4) == "1:02:03.40"
    assert m.clock(59.6, precise=False) == "1:00"
    assert m.length_text(2.75) == "2.8 s"
    assert m.length_text(125) == "2:05"
