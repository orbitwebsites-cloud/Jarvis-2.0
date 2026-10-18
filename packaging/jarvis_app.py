"""What Jarvis.app runs when opened.

Output goes to ~/.jarvis/jarvis.log, since an app opened from Finder or the
Start menu has no terminal. `Jarvis --selftest` imports everything the app needs and exits;
the build runs it to catch anything missing from the bundle.
"""

import sys
from pathlib import Path


def selftest() -> None:
    import importlib

    modules = [
        "jarvis.main", "jarvis.notch", "jarvis.app", "jarvis.voice", "jarvis.audio",
        "jarvis.brain", "jarvis.screen", "jarvis.mail", "jarvis.macapps", "jarvis.setup",
        "jarvis.devices",
        "faster_whisper", "ctranslate2", "onnxruntime", "kokoro_onnx", "sounddevice",
        "pynput.keyboard", "pynput.mouse", "mss", "PIL", "anthropic", "openai",
    ]
    if sys.platform == "win32":
        # Kokoro is an optional extra voice on Windows; the system voice works without it.
        skip = ("jarvis.notch", "jarvis.app", "jarvis.macapps", "kokoro_onnx")
        modules = [m for m in modules if m not in skip]
        modules += [
            "jarvis.winapp", "webview", "pystray", "pycaw.pycaw", "win32clipboard",
            "win32com.client", "pythoncom",
        ]
    else:
        modules += ["AppKit", "WebKit", "Quartz", "PyObjCTools.AppHelper"]
    failed = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as error:  # noqa: BLE001 - report every failure
            failed.append(f"{name}: {error}")
    if sys.platform == "win32":
        from jarvis import winapp as app
    else:
        from jarvis import app

    if not app.HTML.exists():
        failed.append(f"missing {app.HTML}")
    if failed:
        print("Self-test FAILED:\n  " + "\n  ".join(failed))
        sys.exit(1)
    print("Self-test passed.")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        if sys.stdout is None:  # windowed Windows build: no console to print to
            sys.stdout = sys.stderr = open(
                Path.home() / ".jarvis-selftest.txt", "w", encoding="utf-8", errors="replace"
            )
        selftest()
        sys.exit(0)
    log_dir = Path.home() / ".jarvis"
    log_dir.mkdir(parents=True, exist_ok=True)
    # UTF-8, not the Windows code page: a reply with "→" or an emoji must not
    # fail just because it's being written to the log.
    log = open(log_dir / "jarvis.log", "a", buffering=1, encoding="utf-8", errors="replace")
    sys.stdout = sys.stderr = log

    from jarvis.main import main

    main()
