"""Things Jarvis does on your computer: paste text, take screenshots, speak, open things."""

import base64
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import webbrowser

from .config import config


def paste_text(text: str) -> None:
    """Puts text into whatever app has focus, via the clipboard.

    Your previous clipboard contents are restored afterwards.
    """
    if config.is_windows:
        _paste_text_windows(text)
        return
    import pyperclip
    from pynput.keyboard import Controller, Key

    try:
        previous = pyperclip.paste()
    except pyperclip.PyperclipException:
        previous = None

    pyperclip.copy(text)
    keyboard = Controller()
    modifier = Key.cmd if config.is_mac else Key.ctrl
    with keyboard.pressed(modifier):
        keyboard.press("v")
        keyboard.release("v")

    # Give the target app time to read the clipboard before we put it back.
    time.sleep(0.3)
    if previous is not None:
        pyperclip.copy(previous)


def selected_text() -> str:
    """The text selected in the app in front, or "" if nothing is selected.

    Copies the selection with Cmd+C and puts your clipboard back afterwards.
    """
    if config.is_windows:
        return _selected_text_windows()
    import pyperclip

    try:
        previous = pyperclip.paste()
    except pyperclip.PyperclipException:
        previous = None
    marker = "\u2063jarvis-no-selection\u2063"
    pyperclip.copy(marker)
    press_keys("cmd+c")
    selection = ""
    for _ in range(10):  # give the app up to half a second to copy
        time.sleep(0.05)
        current = pyperclip.paste()
        if current != marker:
            selection = current
            break
    if previous is not None:
        pyperclip.copy(previous)
    return selection


def read_clipboard() -> str:
    import pyperclip

    return pyperclip.paste()


def screenshot_png_base64(max_width: int = 1568) -> str:
    """Captures the main screen, scaled down so it isn't wasteful to send."""
    import mss
    from PIL import Image

    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        image = Image.frombytes("RGB", shot.size, shot.rgb)

    if image.width > max_width:
        height = round(image.height * max_width / image.width)
        image = image.resize((max_width, height))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.standard_b64encode(buffer.getvalue()).decode("ascii")


def speak(text: str) -> None:
    from . import voice

    voice.speak(text)


def open_url(url: str) -> None:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)


def open_app(name: str) -> None:
    if config.is_mac:
        subprocess.run(["open", "-a", name], check=True)
    elif config.is_windows:
        _open_app_windows(name)
    else:
        subprocess.Popen([name], start_new_session=True)


BROWSERS = {
    "Safari", "Google Chrome", "Arc", "Firefox", "Microsoft Edge", "Brave Browser",
    "Opera", "Vivaldi", "Chromium", "Zen", "Orion", "Dia",
}

SEARCH_SITES = {
    "google": "https://www.google.com/search?q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={}",
    "amazon": "https://www.amazon.com/s?k={}",
    "maps": "https://www.google.com/maps/search/{}",
    "images": "https://www.google.com/search?tbm=isch&q={}",
}


def search_url(query: str, site: str = "google") -> str:
    template = SEARCH_SITES.get(site, SEARCH_SITES["google"])
    return template.format(urllib.parse.quote_plus(query))


def search_web(query: str, site: str = "google") -> None:
    webbrowser.open(search_url(query, site))


def frontmost_app() -> str:
    """Name of the app in front, or "" if it can't be told."""
    if config.is_windows:
        try:
            return _windows_foreground()[0]
        except Exception:
            return ""
    if not config.is_mac:
        return ""
    try:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return str(app.localizedName()) if app else ""
    except Exception:
        return ""


# When Jarvis last pressed keys itself, so its own Esc isn't mistaken for the
# user asking it to stop.
last_keys_at = 0.0


def press_keys(combo: str) -> None:
    """Presses a shortcut such as "cmd+t" or "cmd+shift+t" in the app in front.

    On Windows and Linux "cmd" means Ctrl, so the same shortcuts work there.
    """
    from pynput.keyboard import Controller, Key

    names = {
        "cmd": Key.cmd if config.is_mac else Key.ctrl,
        "command": Key.cmd if config.is_mac else Key.ctrl,
        "ctrl": Key.ctrl, "control": Key.ctrl,
        "alt": Key.alt, "option": Key.alt,
        "shift": Key.shift,
        "win": Key.cmd, "windows": Key.cmd, "super": Key.cmd, "meta": Key.cmd,
        "enter": Key.enter, "return": Key.enter, "esc": Key.esc, "escape": Key.esc,
        "tab": Key.tab, "space": Key.space, "backspace": Key.backspace,
        # A Mac's "delete" key is backspace; elsewhere it's the forward delete.
        "delete": Key.backspace if config.is_mac else Key.delete, "del": Key.delete,
        "left": Key.left, "right": Key.right, "up": Key.up, "down": Key.down,
        "home": Key.home, "end": Key.end, "pageup": Key.page_up, "pagedown": Key.page_down,
        "pgup": Key.page_up, "pgdn": Key.page_down,
    }
    for optional in ("insert", "print_screen", "caps_lock", "menu"):
        if hasattr(Key, optional):
            names[optional.replace("_", "")] = getattr(Key, optional)
    for number in range(1, 13):
        names[f"f{number}"] = getattr(Key, f"f{number}")
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("No keys given")
    keys = []
    for part in parts:
        if part in names:
            keys.append(names[part])
        elif len(part) == 1:
            keys.append(part)
        else:
            raise ValueError(f"Unknown key: {part}")

    global last_keys_at
    # Let go of the hotkey that triggered Jarvis before pressing anything.
    time.sleep(0.15)
    last_keys_at = time.monotonic()
    keyboard = Controller()
    *held, last = keys
    for key in held:
        keyboard.press(key)
    keyboard.press(last)
    keyboard.release(last)
    for key in reversed(held):
        keyboard.release(key)


def browser_shortcut(combo: str) -> str:
    """Sends a shortcut to the browser if one is in front; returns what happened."""
    app = frontmost_app()
    if (config.is_mac or config.is_windows) and app and app not in BROWSERS:
        return f"not_browser:{app}"
    press_keys(combo)
    return app or "browser"


def new_tab(url: str = "") -> None:
    """Opens a new browser tab, with a page in it if given."""
    if url:
        open_url(url)
        return
    if browser_shortcut("cmd+t").startswith("not_browser:"):
        # No browser in front: open the default browser on a fresh page.
        webbrowser.open("https://www.google.com")


MEDIA_KEYS = {"play_pause": "media_play_pause", "next": "media_next", "previous": "media_previous"}


def media(action: str) -> None:
    from pynput.keyboard import Controller, Key

    key = getattr(Key, MEDIA_KEYS[action])
    keyboard = Controller()
    keyboard.press(key)
    keyboard.release(key)


def set_volume(level: int | None = None, change: int = 0, mute: bool | None = None) -> int:
    """Sets the output volume (0-100) and returns the new level (-1 if unchanged)."""
    if config.is_windows:
        return _set_volume_windows(level, change, mute)
    if not config.is_mac:
        raise RuntimeError("Volume control only works on a Mac and Windows for now.")
    if mute is not None:
        state = "true" if mute else "false"
        subprocess.run(["osascript", "-e", f"set volume output muted {state}"], check=True)
    if level is None and change:
        current = subprocess.run(
            ["osascript", "-e", "output volume of (get volume settings)"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        level = int(current or 50) + change
    if level is not None:
        level = max(0, min(100, level))
        subprocess.run(["osascript", "-e", f"set volume output volume {level}"], check=True)
        return level
    return -1


def notify(title: str, message: str) -> None:
    """Shows a desktop notification, falling back to the terminal."""
    try:
        from plyer import notification

        notification.notify(title=title, message=message[:256], app_name="Jarvis")
    except Exception:
        pass
    print(f"[{title}] {message}")


# ---- Windows ----------------------------------------------------------------

CREATE_NO_WINDOW = 0x08000000

# What people call an app -> its name in the Start menu.
APP_ALIASES = {
    "chrome": "google chrome", "edge": "microsoft edge", "explorer": "file explorer",
    "files": "file explorer", "file manager": "file explorer", "vs code": "visual studio code",
    "vscode": "visual studio code", "calc": "calculator", "cmd": "command prompt",
    "powershell": "windows powershell", "store": "microsoft store", "teams": "microsoft teams",
}
# App names may only use ordinary characters, so nothing in one can be read
# as a command.
APP_NAME = re.compile(r"[\w .'+()-]{1,80}")

_start_apps: dict[str, str] | None = None
_start_apps_at = 0.0
_start_apps_lock = threading.Lock()


def _windows_start_apps(refresh: bool = False) -> dict[str, str]:
    """Start menu app names (lowercase) -> AppID. Slow the first time, then cached."""
    global _start_apps, _start_apps_at
    with _start_apps_lock:
        stale = time.monotonic() - _start_apps_at > 60
        if _start_apps is None or (refresh and stale):
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW, timeout=30,
            ).stdout
            apps = json.loads(out or "[]")
            if isinstance(apps, dict):
                apps = [apps]
            _start_apps = {
                a["Name"].strip().lower(): a["AppID"] for a in apps if a.get("Name") and a.get("AppID")
            }
            _start_apps_at = time.monotonic()
        return _start_apps


def warm_up() -> None:
    """Reads the app list in the background so "open ..." is quick later."""
    if config.is_windows:
        threading.Thread(target=_warm_start_apps, daemon=True).start()


def _warm_start_apps() -> None:
    try:
        _windows_start_apps()
    except Exception as error:
        print(f"Couldn't read the Start menu apps ({error}).")


def _find_start_app(name: str, apps: dict[str, str]) -> str | None:
    key = re.sub(r"^(?:the|my)\s+", "", name.strip().lower())
    key = re.sub(r"\s+app$", "", key)
    key = APP_ALIASES.get(key, key)
    if key in apps:
        return apps[key]
    words = key.split()
    matches = [n for n in apps if all(w in n.split() for w in words)] or [n for n in apps if key in n]
    return apps[min(matches, key=len)] if matches else None


def _app_path(name: str) -> str | None:
    """A program registered under App Paths, such as chrome or excel."""
    import winreg

    exe = name if name.lower().endswith(".exe") else f"{name}.exe"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                return winreg.QueryValue(key, None) or None
        except OSError:
            continue
    return None


def _open_app_windows(name: str) -> None:
    name = name.strip()
    if not APP_NAME.fullmatch(name):
        raise ValueError(f"{name!r} doesn't look like an app name.")
    if name.lower() in ("settings", "windows settings", "pc settings"):
        os.startfile("ms-settings:")
        return
    app_id = _find_start_app(name, _windows_start_apps())
    if app_id is None:  # maybe it was installed since the list was read
        app_id = _find_start_app(name, _windows_start_apps(refresh=True))
    if app_id:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"], creationflags=CREATE_NO_WINDOW)
        return
    exe = shutil.which(name) or _app_path(name)
    if exe:
        subprocess.Popen([exe])
        return
    raise FileNotFoundError(f"There's no app called {name} on this PC.")


# Program file -> the name people know it by. Browsers must match BROWSERS.
WINDOWS_APP_NAMES = {
    "chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge", "firefox.exe": "Firefox",
    "brave.exe": "Brave Browser", "opera.exe": "Opera", "vivaldi.exe": "Vivaldi",
    "arc.exe": "Arc", "zen.exe": "Zen", "chromium.exe": "Chromium",
    "code.exe": "Visual Studio Code", "cursor.exe": "Cursor", "winword.exe": "Microsoft Word",
    "excel.exe": "Microsoft Excel", "powerpnt.exe": "Microsoft PowerPoint", "outlook.exe": "Outlook",
    "olk.exe": "Outlook", "onenote.exe": "OneNote", "notepad.exe": "Notepad", "slack.exe": "Slack",
    "discord.exe": "Discord", "ms-teams.exe": "Microsoft Teams", "teams.exe": "Microsoft Teams",
    "windowsterminal.exe": "Windows Terminal", "explorer.exe": "File Explorer",
    "whatsapp.exe": "WhatsApp", "spotify.exe": "Spotify", "telegram.exe": "Telegram",
}
CONSOLE_WINDOW_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow"}


def _windows_foreground() -> tuple[str, str]:
    """(app name, window class) of the window in front."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", ""
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetClassNameW(hwnd, buffer, 512)
    window_class = buffer.value
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    process = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if process:
        try:
            size = wintypes.DWORD(512)
            if kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                exe = os.path.basename(buffer.value).lower()
        finally:
            kernel32.CloseHandle(process)
    if exe == "applicationframehost.exe":  # Store apps (Calculator, Settings...)
        user32.GetWindowTextW(hwnd, buffer, 512)
        return buffer.value, window_class
    return WINDOWS_APP_NAMES.get(exe, os.path.splitext(exe)[0]), window_class


def _set_volume_windows(level: int | None, change: int, mute: bool | None) -> int:
    import comtypes
    from pycaw.pycaw import AudioUtilities

    try:
        comtypes.CoInitialize()
    except OSError:
        pass  # this thread already set COM up
    volume = AudioUtilities.GetSpeakers().EndpointVolume
    if mute is not None:
        volume.SetMute(int(mute), None)
    if level is None and change:
        level = round(volume.GetMasterVolumeLevelScalar() * 100) + change
    if level is None:
        return -1
    level = max(0, min(100, level))
    volume.SetMasterVolumeLevelScalar(level / 100, None)
    if level > 0 and mute is None:
        volume.SetMute(0, None)  # turning it up should make it audible
    return level


# Clipboard formats Jarvis saves and puts back around its own copy and paste:
# text, copied files, images, web and rich text.
CF_UNICODETEXT, CF_HDROP, CF_DIB, CF_DIBV5 = 13, 15, 8, 17
SAVED_FORMAT_NAMES = ("PNG", "HTML Format", "Rich Text Format", "Preferred DropEffect", "Shell IDList Array")


def _open_clipboard():
    import win32clipboard

    for _ in range(20):  # another app may have it open for a moment
        try:
            win32clipboard.OpenClipboard()
            return win32clipboard
        except Exception:
            time.sleep(0.025)
    raise RuntimeError("The clipboard is busy.")


def _clipboard_snapshot() -> list[tuple[int, object]]:
    clipboard = _open_clipboard()
    try:
        formats = [CF_UNICODETEXT, CF_HDROP, CF_DIB, CF_DIBV5]
        formats += [clipboard.RegisterClipboardFormat(name) for name in SAVED_FORMAT_NAMES]
        saved = []
        for fmt in formats:
            if clipboard.IsClipboardFormatAvailable(fmt):
                try:
                    saved.append((fmt, clipboard.GetClipboardData(fmt)))
                except Exception:
                    pass
        return saved
    finally:
        clipboard.CloseClipboard()


def _clipboard_put(items: list[tuple[int, object]]) -> None:
    import struct

    clipboard = _open_clipboard()
    try:
        clipboard.EmptyClipboard()
        for fmt, data in items:
            if fmt == CF_HDROP:  # a list of files: rebuild the DROPFILES block
                files = "".join(f"{path}\0" for path in data) + "\0"
                data = struct.pack("<IiiII", 20, 0, 0, 0, 1) + files.encode("utf-16-le")
            try:
                clipboard.SetClipboardData(fmt, data)
            except Exception:
                pass
    finally:
        clipboard.CloseClipboard()


def _clipboard_text() -> str:
    clipboard = _open_clipboard()
    try:
        if clipboard.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return clipboard.GetClipboardData(CF_UNICODETEXT)
        return ""
    finally:
        clipboard.CloseClipboard()


def _paste_text_windows(text: str) -> None:
    saved = _clipboard_snapshot()
    try:
        _clipboard_put([(CF_UNICODETEXT, text)])
        press_keys("ctrl+v")
        time.sleep(0.3)  # let the app read the clipboard before it's put back
    finally:
        _clipboard_put(saved)


def _selected_text_windows() -> str:
    import win32clipboard

    if _windows_foreground()[1] in CONSOLE_WINDOW_CLASSES:
        return ""  # a terminal has no selection to rewrite, and copying is risky there
    saved = _clipboard_snapshot()
    before = win32clipboard.GetClipboardSequenceNumber()
    try:
        # Ctrl+Insert copies like Ctrl+C in Windows apps, but never stops a
        # program running in a terminal (VS Code's included).
        press_keys("ctrl+insert")
        for _ in range(10):  # give the app up to half a second to copy
            time.sleep(0.05)
            if win32clipboard.GetClipboardSequenceNumber() != before:
                time.sleep(0.05)
                return _clipboard_text()
        return ""
    finally:
        _clipboard_put(saved)
