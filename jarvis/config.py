"""Settings for Jarvis, read from environment variables or a .env file."""

import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DATA_DIR = Path(os.environ.get("JARVIS_DATA_DIR", Path.home() / ".jarvis"))

# Settings come from a .env file. Running from the source folder, that's the
# .env next to the jarvis folder. The downloadable app keeps its settings in
# ~/.jarvis/.env instead, which its setup screen writes.
REPO_ENV = Path(__file__).resolve().parent.parent / ".env"
USER_ENV = DATA_DIR / ".env"
IS_APP = bool(getattr(sys, "frozen", False))
if os.environ.get("JARVIS_ENV_FILE"):
    ENV_FILE = Path(os.environ["JARVIS_ENV_FILE"])
    load_dotenv(ENV_FILE)
else:
    ENV_FILE = REPO_ENV if REPO_ENV.exists() and not IS_APP else USER_ENV
    # In the app, what its setup screen saved wins over anything left in the
    # system's environment variables (an old ANTHROPIC_API_KEY, say).
    load_dotenv(ENV_FILE, override=IS_APP)
    load_dotenv(USER_ENV)  # app settings fill any gaps; never override

# Ready-made settings for each AI provider. Every one apart from "anthropic"
# speaks the OpenAI chat-completions format. "custom" covers any other
# OpenAI-compatible service (OpenRouter, Groq, Together, LM Studio, ...) and
# needs JARVIS_BASE_URL, JARVIS_API_KEY and JARVIS_MODEL set by hand.
PROVIDERS = {
    "anthropic": {
        "model": "claude-opus-5-5",
    },
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_env": "NVIDIA_API_KEY",
        # If NVIDIA has retired these, Jarvis picks a current model at startup
        # (see models.py).
        "model": "meta/llama-4-maverick-17b-128e-instruct",
        "vision_model": "meta/llama-4-maverick-17b-128e-instruct",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",  # Ollama ignores the key but the client needs one
        "model": "llama3.2",
        "vision_model": "llama3.2-vision",
    },
    "custom": {},
}

PROVIDER = os.environ.get("JARVIS_PROVIDER", "anthropic").strip().lower()
# Reported when Jarvis starts (by make_brain), so the app can show its setup
# screen instead of quitting before it has a window.
PROVIDER_PROBLEM = (
    "" if PROVIDER in PROVIDERS
    else f"JARVIS_PROVIDER={PROVIDER!r} isn't one of: {', '.join(PROVIDERS)}"
)
_preset = PROVIDERS.get(PROVIDER, {})

TRUE_WORDS = {"1", "true", "yes", "on"}
FALSE_WORDS = {"0", "false", "no", "off"}


def _flag(name: str, default: bool) -> bool:
    """A yes/no setting: 1, true, yes or on; 0, false, no or off."""
    value = os.environ.get(name, "").strip().lower()
    if value in TRUE_WORDS:
        return True
    if value in FALSE_WORDS:
        return False
    if value:
        print(f"Ignoring {name}={value!r}: use 1 or 0.")
    return default


def _number(name: str, default, kind=int):
    """A number setting; a typo falls back to the default instead of stopping Jarvis."""
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return kind(value)
    except ValueError:
        print(f"Ignoring {name}={value!r}: it isn't a number.")
        return default


def _setting(env_name: str, preset_key: str) -> str:
    return os.environ.get(env_name) or _preset.get(preset_key, "")


def _api_key() -> str:
    if os.environ.get("JARVIS_API_KEY"):
        return os.environ["JARVIS_API_KEY"]
    if "api_key_env" in _preset:
        return os.environ.get(_preset["api_key_env"], "")
    return _preset.get("api_key", "")


@dataclass(frozen=True)
class Config:
    provider: str = PROVIDER
    model: str = _setting("JARVIS_MODEL", "model")
    # Ask mode sends a screenshot, so it needs a model that can read images.
    # A model you pick yourself is used for Ask mode too, unless you also set
    # JARVIS_VISION_MODEL.
    vision_model: str = (
        os.environ.get("JARVIS_VISION_MODEL")
        or os.environ.get("JARVIS_MODEL")
        or _preset.get("vision_model")
        or _preset.get("model", "")
    )
    # Whether you picked the models yourself. If not, Jarvis may swap the
    # defaults for ones the provider still offers.
    model_chosen: bool = bool(os.environ.get("JARVIS_MODEL"))
    vision_model_chosen: bool = bool(
        os.environ.get("JARVIS_VISION_MODEL") or os.environ.get("JARVIS_MODEL")
    )
    # Only used by OpenAI-compatible providers; the Anthropic client finds
    # ANTHROPIC_API_KEY by itself.
    base_url: str = _setting("JARVIS_BASE_URL", "base_url")
    api_key: str = _api_key()

    whisper_model: str = os.environ.get("JARVIS_WHISPER_MODEL", "base.en")
    speak_replies: bool = _flag("JARVIS_SPEAK", True)
    # Voice: "kokoro" (natural AI voice, runs locally), "system" (built in),
    # or "auto" (Kokoro when installed). JARVIS_VOICE names the voice, e.g.
    # bm_george for Kokoro or Daniel for macOS.
    voice_engine: str = os.environ.get("JARVIS_VOICE_ENGINE", "auto").lower()
    voice: str = os.environ.get("JARVIS_VOICE", "")
    voice_speed: float = _number("JARVIS_VOICE_SPEED", 1.0, float)
    # Which microphone to record from: part of its name, or its number.
    # Empty means the system's default input.
    input_device: str = os.environ.get("JARVIS_INPUT_DEVICE", "").strip()
    # Whisper already adds punctuation, so dictation can skip the AI tidy-up
    # (filler words stay in) to save a round trip to the model.
    clean_dictation: bool = _flag("JARVIS_CLEAN_DICTATION", True)
    # Lets Ollama models think before answering: sometimes smarter, much slower.
    thinking: bool = _flag("JARVIS_THINKING", False)
    # Smaller screenshots make Ask mode much faster on local models, which
    # spend most of their time reading the image.
    screenshot_width: int = _number(
        "JARVIS_SCREENSHOT_WIDTH", 1568 if PROVIDER == "anthropic" else 1024
    )
    # Screen control: how big the screenshots are, whether to draw a
    # coordinate grid on them (helps open models click accurately), and how
    # many steps a task may take.
    screen_control_width: int = _number("JARVIS_SCREEN_WIDTH", 1280)
    screen_grid: bool = _flag("JARVIS_SCREEN_GRID", PROVIDER != "anthropic")
    max_screen_steps: int = _number("JARVIS_MAX_SCREEN_STEPS", 25)

    # Email: your address and an app password (not your normal password).
    email_address: str = os.environ.get("EMAIL_ADDRESS", "")
    email_app_password: str = os.environ.get("EMAIL_APP_PASSWORD", "").replace(" ", "")
    email_imap_host: str = os.environ.get("EMAIL_IMAP_HOST", "")

    # Your dictionary: names and words Jarvis should hear and spell correctly,
    # separated by commas. Also editable in the app.
    vocabulary: str = os.environ.get("JARVIS_VOCABULARY", "")

    # Which calendar new events go in; empty means the first one you can edit.
    calendar_name: str = os.environ.get("JARVIS_CALENDAR", "")

    # TVs and speakers on your Wi-Fi (Chromecast, Sonos, Roku). 0 turns it off.
    devices: bool = _flag("JARVIS_DEVICES", True)
    # Home Assistant: its address and a long-lived access token (made on your
    # profile page in Home Assistant). Also how Jarvis reaches Alexa.
    home_assistant_url: str = os.environ.get("HOME_ASSISTANT_URL", "")
    home_assistant_token: str = os.environ.get("HOME_ASSISTANT_TOKEN", "")

    # So Jarvis can text and call you. On a Mac it uses your own iMessage and
    # FaceTime; Twilio (below) gives it a real separate number instead.
    my_number: str = os.environ.get("JARVIS_MY_NUMBER", "")
    twilio_sid: str = os.environ.get("TWILIO_SID", "")
    twilio_token: str = os.environ.get("TWILIO_TOKEN", "")
    twilio_from: str = os.environ.get("TWILIO_FROM", "")

    sample_rate: int = 16000

    # On a Mac, hold a key to talk and let go to send. Choose from fn,
    # right_cmd, right_option, right_ctrl and right_shift.
    hold_keys: bool = platform.system() == "Darwin" and _flag("JARVIS_HOLD_KEYS", True)
    # One key for everything (Jarvis works out dictate / ask / command from
    # what you say), or JARVIS_ONE_KEY=0 for a separate key per mode.
    one_key: bool = _flag("JARVIS_ONE_KEY", True)
    jarvis_hold: str = os.environ.get("JARVIS_HOLD", "right_option")
    dictate_hold: str = os.environ.get("JARVIS_DICTATE_HOLD", "fn")
    ask_hold: str = os.environ.get("JARVIS_ASK_HOLD", "right_cmd")
    command_hold: str = os.environ.get("JARVIS_COMMAND_HOLD", "right_option")

    # Elsewhere, or with JARVIS_HOLD_KEYS=0, hotkeys are written like
    # "<ctrl>+<alt>+d". Press once to start listening, press again to stop.
    dictate_hotkey: str = os.environ.get("JARVIS_DICTATE_KEY", "<ctrl>+<alt>+d")
    ask_hotkey: str = os.environ.get("JARVIS_ASK_KEY", "<ctrl>+<alt>+a")
    command_hotkey: str = os.environ.get("JARVIS_COMMAND_KEY", "<ctrl>+<alt>+j")

    # The Jarvis app window with the orb and settings (Mac and Windows).
    app_window: bool = _flag("JARVIS_APP", True)

    data_dir: Path = DATA_DIR
    is_mac: bool = platform.system() == "Darwin"
    is_windows: bool = platform.system() == "Windows"


config = Config()
