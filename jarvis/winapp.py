"""The Jarvis app window on Windows: the same app.html as the Mac app, shown
in an Edge WebView2 window through pywebview, plus a tray icon for opening
the window and quitting.

WindowsHUD has the same methods as ConsoleHUD and NotchHUD. It forwards each
state change to the page as the same events the Mac notch sends, and still
shows desktop notifications so replies are visible with the window closed.
pywebview needs the main thread, so run() starts the window there; the hotkey
listener keeps running on its own thread.
"""

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

from . import prefs, voice
from .hud import ConsoleHUD

HTML = Path(__file__).with_name("app.html")
ICON = next(
    (p for p in (Path(__file__).with_name("icon.png"), Path(__file__).parent.parent / "packaging" / "icon.png") if p.exists()),
    Path(__file__).with_name("icon.png"),
)
WIDTH, HEIGHT = 420, 640


def _hint() -> str:
    """The idle line under the orb, with this machine's hotkey."""
    from .config import config

    keys = "+".join(part.strip("<>").capitalize() for part in config.command_hotkey.split("+"))
    return f"Press <b>{keys}</b> and talk"


class _Bridge:
    """What the page reaches as window.pywebview.api."""

    def __init__(self, hud: "WindowsHUD"):
        self._hud = hud

    def post(self, message: str) -> None:
        try:
            self._hud.handle(json.loads(message))
        except Exception as error:
            print(f"App message failed: {error}")


class WindowsHUD(ConsoleHUD):
    def __init__(self, info: dict | None = None):
        self.info = info or {}
        self.window = None
        self.ready = False
        self.pending: list[dict] = []
        self._lock = threading.Lock()
        self._level: Callable[[], float] = lambda: 0.0
        self._listening = False
        self._tray = None

    # ---- window ------------------------------------------------------------

    def attach_app(self, info: dict) -> None:
        self.info = info

    def _create_window(self):
        import webview

        x = y = None
        try:
            import ctypes

            user32 = ctypes.windll.user32
            x = user32.GetSystemMetrics(0) - WIDTH - 24
            y = 24
        except Exception:
            pass
        self.window = webview.create_window(
            "Jarvis",
            html=HTML.read_text(encoding="utf-8"),
            js_api=_Bridge(self),
            width=WIDTH,
            height=HEIGHT,
            x=x,
            y=y,
            min_size=(340, 420),
            background_color="#04060A",
        )
        # Closing the window hides it; Jarvis keeps running in the tray.
        self.window.events.closing += self._on_closing

    def _on_closing(self):
        if getattr(self, "_quitting", False):
            return True
        self.window.hide()
        return False

    def show(self) -> None:
        if self.window is not None:
            self.window.show()
            self.window.restore()

    def quit(self) -> None:
        self._quitting = True
        voice.stop()
        if self._tray is not None:
            self._tray.stop()
        try:
            self.window.destroy()
        finally:
            os._exit(0)

    def _start_tray(self) -> None:
        try:
            import pystray
            from PIL import Image
        except ImportError:
            return
        try:
            image = Image.open(ICON)
        except Exception:
            image = Image.new("RGB", (64, 64), (40, 160, 255))
        menu = pystray.Menu(
            pystray.MenuItem("Open Jarvis", lambda: self.show(), default=True),
            pystray.MenuItem("Quit Jarvis", lambda: self.quit()),
        )
        self._tray = pystray.Icon("Jarvis", image, "Jarvis", menu)
        self._tray.run_detached()

    def send(self, event: dict) -> None:
        """Passes an event to the page. Safe from any thread."""
        with self._lock:
            if not self.ready or self.window is None:
                if event.get("type") != "level":
                    self.pending.append(event)
                return
        try:
            self.window.evaluate_js(f"window.jarvis && window.jarvis.receive({json.dumps(event)})")
        except Exception:
            pass

    # ---- messages from the page ----------------------------------------------

    def handle(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "ready":
            with self._lock:
                self.ready = True
                pending, self.pending = self.pending, []
            self.send(
                {
                    "type": "init",
                    "settings": prefs.all_values(),
                    "voices": voice.available_voices(),
                    "info": {**self.info, "engine": voice.engine(), "hint": _hint()},
                }
            )
            for event in pending:
                self.send(event)
        elif kind == "setup":
            from . import setup

            try:
                setup.write_settings(
                    message.get("provider", ""), message.get("key", ""), message.get("model", "")
                )
            except ValueError as error:
                self.send({"type": "setup_error", "text": str(error)})
                return
            self.send({"type": "setup_done"})
            setup.relaunch()
        elif kind == "set":
            prefs.set(message["key"], message["value"])
            if message["key"] == "voice":
                voice.warm_up()
        elif kind == "test_voice":
            threading.Thread(
                target=voice.speak,
                args=("Good evening. All systems are online, and I'm ready when you are.",),
                daemon=True,
            ).start()

    # ---- HUD methods (any thread) --------------------------------------------

    def run(self, main: Callable[[], None]) -> None:
        import webview

        self._create_window()
        self._start_tray()
        threading.Thread(target=self._pump_levels, daemon=True).start()
        webview.start()  # blocks on the main thread until quit
        self.quit()

    def _pump_levels(self) -> None:
        import time

        while True:
            if self._listening:
                self.send({"type": "level", "value": round(self._level(), 3)})
            time.sleep(0.05)

    def set_level_source(self, level: Callable[[], float]) -> None:
        self._level = level

    def listening(self, mode: str) -> None:
        self._listening = True
        self.send({"type": "state", "state": "listening"})

    def thinking(self, heard: str) -> None:
        self._listening = False
        self.send({"type": "state", "state": "thinking"})
        if heard:
            self.send({"type": "heard", "text": heard})

    def reply(self, text: str, heard: str = "") -> None:
        self.send({"type": "reply", "text": text})
        self.send({"type": "state", "state": "idle"})
        super().reply(text, heard)

    def flash(self, text: str) -> None:
        self._listening = False
        self.send({"type": "step", "title": "Jarvis", "text": text})
        self.send({"type": "state", "state": "idle"})

    def error(self, text: str) -> None:
        self._listening = False
        self.send({"type": "error", "text": text})
        self.send({"type": "state", "state": "idle"})
        super().error(text)

    def status(self, title: str, body: str) -> None:
        self.send({"type": "state", "state": "working"})
        self.send({"type": "step", "title": title, "text": body})

    def speaking(self, active: bool) -> None:
        self.send({"type": "state", "state": "speaking" if active else "idle"})


def run_setup_window(problem: str) -> None:
    """Shows the setup screen in the Jarvis window until the person saves."""
    hud = WindowsHUD({"provider": "", "model": "", "vision_model": "", "setup": problem})
    hud.run(lambda: None)
