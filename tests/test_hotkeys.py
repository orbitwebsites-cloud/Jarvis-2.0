from types import SimpleNamespace as NS

import pytest

from jarvis.hotkeys import HotkeyMatcher, key_matches, parse


def test_parse():
    assert parse("<ctrl>+<alt>+d") == (frozenset({"ctrl", "alt"}), "d")
    assert parse("<cmd>+<shift>+<space>") == (frozenset({"cmd", "shift"}), "space")
    with pytest.raises(ValueError):
        parse("<ctrl>+<alt>")
    with pytest.raises(ValueError):
        parse("<hyper>+d")


def test_mac_matches_by_key_code_even_when_option_changes_the_character():
    option_d = NS(vk=2, char="∂")
    assert key_matches(option_d, "d", "mac")
    assert not key_matches(NS(vk=0, char="d"), "d", "mac")


def test_windows_matches_control_characters_by_key_code():
    ctrl_d = NS(vk=0x44, char="\x04")
    assert key_matches(ctrl_d, "d", "windows")


def test_matcher_needs_exactly_the_right_modifiers():
    fired = []
    mods = {"CTRL": "ctrl", "ALT": "alt", "SHIFT": "shift"}
    matcher = HotkeyMatcher(
        {"<ctrl>+<alt>+d": lambda: fired.append("d")},
        "mac",
        lambda key: mods.get(key) if isinstance(key, str) else None,
    )
    d = NS(vk=2, char="∂")

    matcher.press("CTRL")
    matcher.press(d)
    assert fired == []  # Alt not held yet

    matcher.press("ALT")
    matcher.press(d)
    assert fired == ["d"]

    matcher.press("SHIFT")
    matcher.press(d)
    assert fired == ["d"]  # an extra modifier doesn't count

    matcher.release("SHIFT")
    matcher.release("ALT")
    matcher.press(d)
    assert fired == ["d"]


def test_holding_the_hotkey_fires_once_despite_key_repeat():
    fired = []
    mods = {"CTRL": "ctrl", "ALT": "alt"}
    matcher = HotkeyMatcher(
        {"<ctrl>+<alt>+j": lambda: fired.append("j")},
        "windows",
        lambda key: mods.get(key) if isinstance(key, str) else None,
    )
    j = NS(vk=0x4A, char="\n")

    matcher.press("CTRL")
    matcher.press("ALT")
    for _ in range(30):  # Windows auto-repeat while the key is held
        matcher.press(j)
    assert fired == ["j"]

    matcher.release(j)
    matcher.press(j)  # a real second press still toggles
    assert fired == ["j", "j"]


def _windows_matcher(fired, **kwargs):
    mods = {"CTRL": "ctrl", "ALT": "alt"}
    return HotkeyMatcher(
        {"<ctrl>+<alt>+j": lambda: fired.append("j")},
        "windows",
        lambda key: mods.get(key) if isinstance(key, str) else None,
        **kwargs,
    )


def test_hotkey_sent_by_a_macro_tool_still_works():
    # Logitech G HUB or AutoHotkey send the keys as "injected" input.
    fired = []
    matcher = _windows_matcher(fired)
    matcher.press("CTRL")
    matcher.press("ALT")
    matcher.press(NS(vk=0x4A, char="\n"), True)
    assert fired == ["j"]


def test_escape_calls_on_escape_but_not_when_injected():
    fired, stopped = [], []
    esc = NS(vk=0x1B, char=None)
    matcher = _windows_matcher(fired, on_escape=lambda: stopped.append(1), is_escape=lambda k: k is esc)
    matcher.press(esc, True)
    assert stopped == []
    matcher.press(esc)
    assert stopped == [1]


def test_modifiers_released_where_jarvis_cant_see_are_dropped():
    # Ctrl+Alt+Del: the key-ups happen on the secure desktop, so the hook
    # never sees them. Typing a plain "j" afterwards must not start Jarvis.
    fired = []
    really_down = set()
    matcher = _windows_matcher(fired, modifier_down=lambda m: m in really_down)
    matcher.press("CTRL")
    matcher.press("ALT")
    matcher.press(NS(vk=0x4A, char="j"))
    assert fired == []  # the OS says nothing is held any more
    really_down.update({"ctrl", "alt"})
    matcher.release(NS(vk=0x4A, char="j"))
    matcher.press("CTRL")
    matcher.press("ALT")
    matcher.press(NS(vk=0x4A, char="\n"))
    assert fired == ["j"]


def test_a_failing_action_does_not_stop_later_ones():
    import time

    from jarvis.hotkeys import ActionRunner

    done = []
    runner = ActionRunner()

    def boom():
        raise OSError("Error querying device -1")

    runner.wrap(boom)()
    runner.wrap(lambda: done.append("ok"))()
    for _ in range(50):
        if done:
            break
        time.sleep(0.01)
    assert done == ["ok"]
