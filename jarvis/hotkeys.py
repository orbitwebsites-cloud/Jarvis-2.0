"""Global hotkeys matched by physical key, not by the character typed.

pynput's GlobalHotKeys compares characters, and on a Mac holding Option
changes the character (Option+D types "∂"), so combinations with Option can
silently never fire. Matching the key code avoids that.
"""

import queue
import threading
import traceback
from collections.abc import Callable

# macOS virtual key codes for an ANSI (US-style) keyboard.
MAC_KEY_CODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19,
    "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31,
    "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
    "space": 49,
}

MODIFIER_NAMES = {"ctrl", "alt", "shift", "cmd"}


def parse(combo: str) -> tuple[frozenset[str], str]:
    """'<ctrl>+<alt>+d' -> ({'ctrl', 'alt'}, 'd')."""
    parts = [p.strip().strip("<>").lower() for p in combo.split("+")]
    *modifiers, key = parts
    if key in MODIFIER_NAMES:
        raise ValueError(f"Hotkey {combo!r} has no key besides modifiers")
    unknown = set(modifiers) - MODIFIER_NAMES
    if unknown:
        raise ValueError(f"Unknown modifier in hotkey {combo!r}: {', '.join(sorted(unknown))}")
    return frozenset(modifiers), key


def key_matches(key, name: str, system: str) -> bool:
    """Whether a pynput key event is the key called `name` ('d', 'space', ...)."""
    if getattr(key, "name", None) == name:  # special keys: space, f9, ...
        return True
    vk = getattr(key, "vk", None)
    if system == "mac" and name in MAC_KEY_CODES:
        return vk == MAC_KEY_CODES[name]
    if system == "windows" and len(name) == 1 and name.isalnum():
        return vk == ord(name.upper())
    char = getattr(key, "char", None)
    if char is not None and char.lower() == name:
        return True
    return len(name) == 1 and vk == ord(name)


class HotkeyMatcher:
    """Feeds on key presses and releases; calls a binding when its combo is hit."""

    def __init__(
        self,
        bindings: dict[str, Callable[[], None]],
        system: str,
        modifier_of: Callable[[object], str | None],
        on_escape: Callable[[], None] | None = None,
        is_escape: Callable[[object], bool] = lambda key: False,
        modifier_down: Callable[[str], bool] | None = None,
    ):
        self.bindings = [(*parse(combo), action) for combo, action in bindings.items()]
        self.system = system
        self.modifier_of = modifier_of
        self.on_escape = on_escape
        self.is_escape = is_escape
        # Asks the OS whether a modifier is really down. Key releases that
        # happen on the lock screen or the Ctrl+Alt+Del screen never reach
        # Jarvis, so without this Ctrl and Alt could stay "held" afterwards.
        self.modifier_down = modifier_down
        self.held: set[str] = set()
        self.down: set = set()  # hotkey keys held since they fired

    @staticmethod
    def _identity(key):
        """Stable id for a key across its press, auto-repeats and release."""
        vk = getattr(key, "vk", None)
        if vk is not None:
            return ("vk", vk)
        char = getattr(key, "char", None)
        return ("char", char.lower()) if char else ("key", key)

    def press(self, key, injected: bool = False) -> None:
        modifier = self.modifier_of(key)
        if modifier:
            self.held.add(modifier)
            return
        if self.on_escape is not None and self.is_escape(key):
            # An Esc that software pressed (Jarvis itself, while working)
            # isn't the user asking it to stop. The hotkey itself still works
            # from macro tools such as Logitech G HUB or AutoHotkey.
            if not injected:
                self.on_escape()
            return
        if self.modifier_down is not None:
            self.held = {m for m in self.held if self.modifier_down(m)}
        # Holding a key makes the OS repeat its press (about 30 a second on
        # Windows). Only the first press counts, so holding the hotkey can't
        # toggle listening on and off over and over.
        identity = self._identity(key)
        if identity in self.down:
            return
        for modifiers, name, action in self.bindings:
            if self.held == modifiers and key_matches(key, name, self.system):
                self.down.add(identity)
                action()

    def release(self, key, injected: bool = False) -> None:
        modifier = self.modifier_of(key)
        if modifier:
            self.held.discard(modifier)
            return
        self.down.discard(self._identity(key))


class ActionRunner:
    """Runs hotkey actions one at a time on a thread of their own.

    The keyboard hook's own thread must stay quick: while it's busy (opening
    the microphone can take seconds the first time) every key press on the
    computer waits, and Windows drops hooks that keep it waiting. An action
    that raises is reported instead of stopping the listener for good.
    """

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, name="jarvis-hotkeys", daemon=True).start()

    def wrap(self, action: Callable[[], None]) -> Callable[[], None]:
        return lambda: self._queue.put(action)

    def _run(self) -> None:
        while True:
            action = self._queue.get()
            try:
                action()
            except Exception:
                print("A hotkey action failed:", flush=True)
                traceback.print_exc()


def _windows_modifier_down() -> Callable[[str], bool]:
    import ctypes

    user32 = ctypes.WinDLL("user32")
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    codes = {"ctrl": (0x11,), "alt": (0x12,), "shift": (0x10,), "cmd": (0x5B, 0x5C)}
    return lambda name: any(user32.GetAsyncKeyState(code) & 0x8000 for code in codes.get(name, ()))


def start_listener(
    bindings: dict[str, Callable[[], None]],
    system: str,
    on_escape: Callable[[], None] | None = None,
):
    """Starts listening in the background and returns the pynput listener."""
    from pynput import keyboard

    Key = keyboard.Key
    groups = {
        "ctrl": ("ctrl", "ctrl_l", "ctrl_r"),
        "alt": ("alt", "alt_l", "alt_r", "alt_gr"),
        "shift": ("shift", "shift_l", "shift_r"),
        "cmd": ("cmd", "cmd_l", "cmd_r"),
    }
    lookup = {
        getattr(Key, member): group
        for group, members in groups.items()
        for member in members
        if hasattr(Key, member)
    }

    def modifier_of(key):
        try:
            return lookup.get(key)
        except TypeError:  # unhashable key objects on some platforms
            return None

    runner = ActionRunner()
    matcher = HotkeyMatcher(
        {combo: runner.wrap(action) for combo, action in bindings.items()},
        system,
        modifier_of,
        on_escape=runner.wrap(on_escape) if on_escape else None,
        is_escape=lambda key: key == Key.esc,
        modifier_down=_windows_modifier_down() if system == "windows" else None,
    )
    listener = keyboard.Listener(on_press=matcher.press, on_release=matcher.release)
    listener.start()
    return listener
