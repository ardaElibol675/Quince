import pytest

from quince.input import drag
from quince.input.drag import (
    ButtonPress,
    ButtonRelease,
    DndStarted,
    DragCancel,
    DragEnded,
    DragKey,
    DragStarted,
    DragTracker,
    KeyPress,
    KeyRelease,
    Motion,
    TriggerTapped,
)


def feed_all(tracker, events):
    out = []
    for event in events:
        out.extend(tracker.feed(event))
    return out


def start_drag(tracker, t=0):
    """Button down, XDND starts, pointer moves past the threshold."""
    return feed_all(
        tracker,
        [ButtonPress(1, t, 100, 100), DndStarted(t + 5), Motion(t + 10, 120, 100)],
    )


def tap(key, t, length=50):
    return [KeyPress(key, t), KeyRelease(key, t + length)]


@pytest.fixture
def tracker():
    return DragTracker()


def test_drag_starts_after_threshold_and_dnd(tracker):
    assert feed_all(tracker, [ButtonPress(1, 0, 100, 100), DndStarted(5)]) == []
    assert tracker.feed(Motion(10, 104, 104)) == []  # below threshold
    assert tracker.feed(Motion(20, 110, 100)) == [DragStarted(20)]
    assert tracker.in_drag


def test_dnd_after_threshold_also_starts_drag(tracker):
    feed_all(tracker, [ButtonPress(1, 0, 0, 0), Motion(10, 50, 0)])
    assert not tracker.in_drag
    assert tracker.feed(DndStarted(15)) == [DragStarted(15)]


def test_plain_button_drag_without_dnd_is_not_a_drag(tracker):
    feed_all(tracker, [ButtonPress(1, 0, 0, 0), Motion(10, 200, 0)])
    assert not tracker.in_drag
    assert feed_all(tracker, tap("Shift_L", 20)) == []


def test_dnd_owner_change_without_button_is_ignored(tracker):
    tracker.feed(DndStarted(0))
    feed_all(tracker, [ButtonPress(1, 10, 0, 0), Motion(20, 50, 0)])
    assert not tracker.in_drag


def test_require_dnd_false_counts_any_drag():
    tracker = DragTracker(require_dnd=False)
    assert feed_all(tracker, [ButtonPress(1, 0, 0, 0), Motion(10, 50, 0)]) == [DragStarted(10)]


def test_other_buttons_do_not_start_a_drag(tracker):
    feed_all(tracker, [ButtonPress(3, 0, 0, 0), DndStarted(5), Motion(10, 50, 0)])
    assert not tracker.in_drag


def test_release_ends_drag(tracker):
    start_drag(tracker)
    assert tracker.feed(ButtonRelease(1, 100)) == [DragEnded(100)]
    assert not tracker.in_drag
    assert tracker.feed(ButtonRelease(1, 110)) == []


@pytest.mark.parametrize(
    ("key", "wheel"),
    [
        ("Shift_L", "convert"),
        ("Shift_R", "convert"),
        ("Control_L", "tools"),
        ("Control_R", "tools"),
        ("Alt_L", "actions"),
    ],
)
def test_tap_during_drag(tracker, key, wheel):
    start_drag(tracker)
    assert feed_all(tracker, tap(key, 100)) == [TriggerTapped(wheel, key, 150)]


def test_tap_without_drag_is_ignored(tracker):
    assert feed_all(tracker, tap("Shift_L", 0)) == []


def test_tap_after_drag_ended_is_ignored(tracker):
    start_drag(tracker)
    tracker.feed(ButtonRelease(1, 50))
    assert feed_all(tracker, tap("Shift_L", 100)) == []


def test_key_pressed_before_drag_is_not_a_tap(tracker):
    tracker.feed(KeyPress("Control_L", 0))
    start_drag(tracker, t=10)
    assert tracker.feed(KeyRelease("Control_L", 100)) == []


def test_drag_ending_while_key_held_is_not_a_tap(tracker):
    start_drag(tracker)
    tracker.feed(KeyPress("Shift_L", 100))
    tracker.feed(ButtonRelease(1, 150))
    assert tracker.feed(KeyRelease("Shift_L", 200)) == []


def test_long_press_is_not_a_tap(tracker):
    start_drag(tracker)
    assert feed_all(tracker, tap("Shift_L", 100, length=401)) == []
    assert feed_all(tracker, tap("Shift_L", 600, length=400)) == [
        TriggerTapped("convert", "Shift_L", 1000)
    ]


def test_max_tap_ms_is_configurable():
    tracker = DragTracker(max_tap_ms=100)
    start_drag(tracker)
    assert feed_all(tracker, tap("Shift_L", 100, length=150)) == []


def test_chord_is_not_a_tap(tracker):
    start_drag(tracker)
    events = [
        KeyPress("Control_L", 100),
        KeyPress("keycode:38", 120),
        KeyRelease("keycode:38", 130),
        KeyRelease("Control_L", 140),
    ]
    assert feed_all(tracker, events) == []


def test_two_triggers_together_are_not_a_tap(tracker):
    start_drag(tracker)
    events = [
        KeyPress("Control_L", 100),
        KeyPress("Shift_L", 110),
        KeyRelease("Shift_L", 120),
        KeyRelease("Control_L", 130),
    ]
    assert feed_all(tracker, events) == []


def test_trigger_while_other_key_held_is_not_a_tap(tracker):
    start_drag(tracker)
    tracker.feed(KeyPress("keycode:38", 90))
    assert feed_all(tracker, tap("Shift_L", 100)) == []


def test_button_during_press_cancels_tap(tracker):
    start_drag(tracker)
    events = [KeyPress("Shift_L", 100), ButtonPress(3, 110, 0, 0), KeyRelease("Shift_L", 120)]
    assert feed_all(tracker, events) == [DragCancel(3, 110)]  # cancel, but no tap


def test_auto_repeat_does_not_reset_tap(tracker):
    start_drag(tracker)
    events = [KeyPress("Shift_L", 100), KeyPress("Shift_L", 130), KeyRelease("Shift_L", 150)]
    assert feed_all(tracker, events) == [TriggerTapped("convert", "Shift_L", 150)]


def test_untracked_keys_are_distinct(tracker):
    start_drag(tracker)
    events = [
        KeyPress("keycode:38", 90),
        KeyPress("keycode:39", 91),
        KeyRelease("keycode:38", 92),
    ]
    feed_all(tracker, events)
    # keycode:39 is still held, so a trigger now is part of a chord
    assert feed_all(tracker, tap("Shift_L", 100)) == []


def test_altgr_is_not_a_trigger(tracker):
    start_drag(tracker)
    assert feed_all(tracker, tap("ISO_Level3_Shift", 100)) == []


def test_consecutive_taps_switch_wheels(tracker):
    start_drag(tracker)
    out = feed_all(tracker, tap("Shift_L", 100) + tap("Control_L", 300))
    assert [s.wheel for s in out] == ["convert", "tools"]


@pytest.mark.parametrize("key", ["Escape", "Left", "Right", "Up", "Down", "Return", "Home", "End"])
def test_nav_keys_reported_during_drag(tracker, key):
    assert tracker.feed(KeyPress(key, 0)) == []
    tracker.feed(KeyRelease(key, 10))
    start_drag(tracker, t=20)
    assert tracker.feed(KeyPress(key, 100)) == [DragKey(key, 100)]


def test_wants_motion_only_until_threshold(tracker):
    assert not tracker.wants_motion
    tracker.feed(ButtonPress(1, 0, 0, 0))
    assert tracker.wants_motion
    tracker.feed(Motion(10, 50, 0))
    assert not tracker.wants_motion


@pytest.mark.parametrize("key", ["Super_L", "Super_R", "Hyper_L"])
def test_super_is_rejected(key):
    with pytest.raises(ValueError, match="cannot be used"):
        DragTracker({"convert": (key,)})


def test_key_bound_twice_is_rejected():
    with pytest.raises(ValueError, match="bound to both"):
        drag.validate_triggers({"convert": ("Shift_L",), "tools": ("Shift_L",)})


def test_default_triggers_never_use_super():
    keys = [k for keys in drag.DEFAULT_TRIGGERS.values() for k in keys]
    assert not any(k.startswith(("Super", "Hyper")) for k in keys)
    assert set(drag.DEFAULT_TRIGGERS) == {"convert", "tools", "actions"}


@pytest.mark.parametrize("button", [2, 3])
def test_middle_and_right_click_cancel_during_drag(tracker, button):
    start_drag(tracker)
    assert tracker.feed(ButtonPress(button, 100, 0, 0)) == [DragCancel(button, 100)]
    assert tracker.in_drag  # button 1 is still held


def test_cancel_click_without_drag_is_ignored(tracker):
    assert tracker.feed(ButtonPress(3, 0, 0, 0)) == []


@pytest.mark.parametrize("button", [4, 5, 6, 7])
def test_scroll_never_cancels_or_breaks_a_tap(tracker, button):
    start_drag(tracker)
    events = [KeyPress("Shift_L", 100), ButtonPress(button, 110, 0, 0), KeyRelease("Shift_L", 120)]
    assert feed_all(tracker, events) == [TriggerTapped("convert", "Shift_L", 120)]


def test_new_triggers_apply_at_once():
    from quince.input import drag as d

    tracker = d.DragTracker({"convert": ("Shift_L",)}, max_tap_ms=400, require_dnd=False)
    tracker.feed(d.ButtonPress(1, 0, 0, 0))
    tracker.feed(d.Motion(10, 50, 0))
    assert tracker.in_drag
    tracker.feed(d.KeyPress("Shift_L", 20))
    tracker.set_triggers({"convert": ("Control_R",), "tools": ("Shift_L",)}, 300)
    # The tap that started under the old keys is dropped.
    assert tracker.feed(d.KeyRelease("Shift_L", 30)) == []
    tracker.feed(d.KeyPress("Shift_L", 40))
    assert tracker.feed(d.KeyRelease("Shift_L", 60)) == [d.TriggerTapped("tools", "Shift_L", 60)]
    tracker.feed(d.KeyPress("Control_R", 100))
    assert tracker.feed(d.KeyRelease("Control_R", 500)) == []  # longer than 300 ms
    assert tracker.trigger_keys == {"Control_R", "Shift_L"}


def test_watcher_hands_new_triggers_to_its_thread(monkeypatch):
    from quince.input import drag as d
    from quince.input.x11 import X11InputWatcher

    tracker = d.DragTracker()
    watcher = X11InputWatcher(tracker, lambda _signal: None)
    refreshed = []
    monkeypatch.setattr(watcher, "_refresh_keymap", refreshed.append)
    with pytest.raises(ValueError):
        watcher.set_triggers({"convert": ("Super_L",)}, 400)
    watcher.set_triggers({"convert": ("Menu",), "tools": (), "actions": ()}, 300)
    assert tracker.trigger_keys != {"Menu"}  # not before the watcher thread takes them
    watcher._take_triggers("display")
    assert tracker.trigger_keys == {"Menu"} and tracker.max_tap_ms == 300
    assert refreshed == ["display"]
    watcher._take_triggers("display")  # nothing new
    assert refreshed == ["display"]
