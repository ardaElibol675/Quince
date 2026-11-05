import pytest

from quince.input.taps import TapDetector


@pytest.fixture
def taps():
    return TapDetector(max_tap_ms=400)


def test_a_quick_tap_of_a_trigger_names_its_wheel(taps):
    taps.press("Shift_L", 0)
    assert taps.release("Shift_L", 100) == "convert"
    taps.press("Control_R", 200)
    assert taps.release("Control_R", 250) == "tools"
    taps.press("Alt_L", 300)
    assert taps.release("Alt_L", 310) == "actions"


def test_a_long_press_is_not_a_tap(taps):
    taps.press("Shift_L", 0)
    assert taps.release("Shift_L", 401) is None


def test_another_key_in_between_is_not_a_tap(taps):
    taps.press("Shift_L", 0)
    taps.press("Down", 10)  # Shift+Down extends the selection
    taps.release("Down", 20)
    assert taps.release("Shift_L", 30) is None


def test_a_trigger_pressed_while_another_key_is_held_is_not_a_tap(taps):
    taps.press("a", 0)
    taps.press("Shift_L", 10)
    assert taps.release("Shift_L", 20) is None


def test_a_click_in_between_is_not_a_tap(taps):
    taps.press("Control_L", 0)
    taps.button()  # Ctrl+click selects files
    assert taps.release("Control_L", 50) is None


def test_auto_repeat_presses_are_ignored(taps):
    taps.press("Shift_L", 0)
    taps.press("Shift_L", 30)
    assert taps.release("Shift_L", 60) == "convert"


def test_non_trigger_keys_and_reset(taps):
    taps.press("Return", 0)
    assert taps.release("Return", 10) is None
    taps.press("Shift_L", 20)
    taps.reset()
    assert taps.release("Shift_L", 30) is None


def test_custom_triggers_and_super_rejected():
    taps = TapDetector({"convert": ["F9"]})
    taps.press("F9", 0)
    assert taps.release("F9", 10) == "convert"
    assert taps.trigger_keys == {"F9"}
    with pytest.raises(ValueError):
        TapDetector({"convert": ["Super_L"]})
