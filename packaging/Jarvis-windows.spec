# PyInstaller recipe for Jarvis.exe on Windows. Build from the packaging folder:
#     pyinstaller --noconfirm Jarvis-windows.spec
# The result is dist\Jarvis\Jarvis.exe (a folder build: faster startup than
# one-file, and Whisper's native libraries load reliably).

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is set by PyInstaller

datas = [
    (str(ROOT / "jarvis" / "app.html"), "jarvis"),
    (str(ROOT / "packaging" / "icon.png"), "jarvis"),  # tray icon
]
binaries = []
hiddenimports = [
    "pynput.keyboard._win32", "pynput.mouse._win32",
    "pystray._win32", "webview.platforms.winforms", "webview.platforms.edgechromium",
    "pyttsx3.drivers", "pyttsx3.drivers.sapi5", "plyer.platforms.win.notification",
    "comtypes", "win32com", "jarvis.winapp",
]
# Packages with native libraries or data files PyInstaller can't find alone.
for package in [
    "faster_whisper", "ctranslate2", "onnxruntime", "tokenizers", "av",
    "kokoro_onnx", "espeakng_loader", "phonemizer", "language_tags", "segments", "csvw",
    "sounddevice", "_sounddevice_data", "pynput", "mss", "webview", "pystray",
    "clr_loader", "pythonnet", "plyer",
]:
    try:
        d, b, h = collect_all(package)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["jarvis_app.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "pytest", "AppKit", "Foundation", "WebKit", "Quartz", "objc"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Jarvis",
    console=False,
    icon=str(Path(SPECPATH) / "Jarvis.ico"),  # noqa: F821 - made by make_icon.py
)
coll = COLLECT(exe, a.binaries, a.datas, name="Jarvis")
