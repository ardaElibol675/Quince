import pytest

from quince.ui.wheel.state import KEYBOARD_EXIT_PX, KeyResult, Mode, WheelState


def test_arrows_wrap_both_ways():
    s = WheelState(3)
    assert s.key("Right") is KeyResult.MOVED and s.highlighted == 0
    s.key("Down")
    assert s.highlighted == 1
    s.key("Right")
    s.key("Right")
    assert s.highlighted == 0
    s.key("Left")
    assert s.highlighted == 2
    s.key("Up")
    assert s.highlighted == 1


def test_first_previous_key_goes_to_last():
    s = WheelState(5)
    s.key("Left")
    assert s.highlighted == 4


def test_home_end():
    s = WheelState(6)
    s.key("End")
    assert s.highlighted == 5
    s.key("Home")
    assert s.highlighted == 0


@pytest.mark.parametrize("key", ["Return", "KP_Enter", "space"])
def test_choose_needs_a_highlight(key):
    s = WheelState(3)
    assert s.key(key) is KeyResult.IGNORED
    s.key("Right")
    assert s.key(key) is KeyResult.CHOOSE


@pytest.mark.parametrize("key", ["Escape", "Tab"])
def test_close_keys(key):
    assert WheelState(0).key(key) is KeyResult.CLOSE
    assert WheelState(4).key(key) is KeyResult.CLOSE


def test_other_keys_are_ignored():
    s = WheelState(3)
    assert s.key("a") is KeyResult.IGNORED
    assert s.key("Shift_L") is KeyResult.IGNORED
    assert s.highlighted is None


def test_empty_and_single():
    empty = WheelState(0)
    assert empty.key("Right") is KeyResult.IGNORED and empty.highlighted is None
    single = WheelState(1)
    single.key("Right")
    single.key("Right")
    assert single.highlighted == 0
    single.key("Left")
    assert single.highlighted == 0


def test_pointer_highlights():
    s = WheelState(4)
    assert s.pointer((0, -100), 0) is True
    assert s.pointer((1, -100), 0) is False
    assert s.pointer((0, 0), None) is True and s.highlighted is None


def test_keyboard_mode_ignores_small_pointer_moves():
    s = WheelState(4)
    s.pointer((0, 0), None)
    s.key("Right")
    assert s.mode is Mode.KEYBOARD and s.highlighted == 0
    # GTK nudges the pointer by 1 px per arrow key.
    for i in range(1, 5):
        assert s.pointer((i, 0), 2) is False
    assert s.highlighted == 0
    assert s.pointer_gone() is False and s.highlighted == 0
    # A clear move hands control back to the pointer.
    assert s.pointer((KEYBOARD_EXIT_PX + 1, 0), 2) is True
    assert s.mode is Mode.POINTER and s.highlighted == 2


def test_keyboard_anchor_set_by_first_pointer_event():
    s = WheelState(4)
    s.key("Right")  # no pointer position known yet
    assert s.pointer((300, 300), 3) is False
    assert s.pointer((305, 300), 3) is False
    assert s.highlighted == 0


def test_pointer_gone_clears_pointer_highlight_and_reset():
    s = WheelState(4)
    s.pointer((0, -100), 1)
    assert s.pointer_gone() is True and s.highlighted is None
    s.key("End")
    s.reset(2)
    assert s.count == 2 and s.highlighted is None and s.mode is Mode.POINTER
