"""Microphone recording and local speech-to-text."""

import threading

import numpy as np

from .config import config


def find_input_device(sd, wanted: str):
    """The microphone JARVIS_INPUT_DEVICE names: its number, or part of its name.

    Windows lists each microphone once per audio system, and the default one
    (MME) cuts names to 31 characters, so "USB PnP Audio Device" appears there
    as "Microphone (USB PnP Audio Devic". The default system's entry is used
    when there is one, since it records at any sample rate.
    """
    if not wanted:
        return None
    if wanted.isdigit():
        return int(wanted)
    inputs = [(i, d) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    full_names = {d["name"].lower() for _, d in inputs if wanted.lower() in d["name"].lower()}
    if not full_names:
        raise ValueError(f"There's no microphone called {wanted!r} (JARVIS_INPUT_DEVICE).")
    default_system = sd.default.hostapi
    for i, d in inputs:
        if d["hostapi"] == default_system and any(n.startswith(d["name"].lower()) for n in full_names):
            return i
    return next(i for i, d in inputs if d["name"].lower() in full_names)


class Recorder:
    """Records from the default microphone between start() and stop()."""

    def __init__(self):
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self._lock = threading.Lock()
        self._announced_device = -1  # which microphone was last logged
        # Loudness of the latest audio chunk, 0 to 1, for the notch waveform.
        self.level = 0.0

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def warm_up(self) -> None:
        """Loads the audio library in the background: the first time takes a
        few seconds, which would otherwise cut off the start of what you say."""
        threading.Thread(target=self._load, daemon=True).start()

    @staticmethod
    def _load():
        import sounddevice as sd

        return sd

    def _open(self):
        sd = self._load()
        device = find_input_device(sd, config.input_device)
        if device != self._announced_device:
            name = sd.query_devices(device if device is not None else sd.default.device[0])["name"]
            print(f"Using microphone: {name}", flush=True)
            self._announced_device = device
        stream = sd.InputStream(
            samplerate=config.sample_rate,
            channels=1,
            dtype="float32",
            device=device,
            callback=self._on_audio,
        )
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        return stream

    def start(self) -> None:
        self._chunks = []
        try:
            stream = self._open()
        except Exception:
            # The microphone list is read once, so a mic plugged in (or a
            # new default chosen) since then is unknown. Re-read it and retry.
            self.refresh_devices()
            stream = self._open()
        # Only now is it recording, so a failed start can't leave it stuck.
        self._stream = stream

    def refresh_devices(self) -> None:
        """Re-reads the list of microphones and which one is the default."""
        sd = self._load()
        try:
            sd._terminate()
            sd._initialize()
        except Exception as error:
            print(f"Couldn't re-read the microphones: {error}", flush=True)

    def _on_audio(self, indata, frames, time, status) -> None:
        with self._lock:
            self._chunks.append(indata.copy())
        rms = float(np.sqrt(np.mean(np.square(indata))))
        self.level = min(1.0, rms * 12)

    def stop(self) -> np.ndarray:
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        stream, self._stream = self._stream, None
        self.level = 0.0
        try:
            stream.stop()
            stream.close()
        except Exception as error:  # e.g. the microphone was unplugged
            print(f"Couldn't close the microphone cleanly: {error}", flush=True)
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._chunks).flatten()


class Transcriber:
    """Runs Whisper on this computer, so audio never leaves it."""

    def __init__(self):
        self._model = None
        # An interrupted request can still be transcribing when the next one
        # starts; Whisper runs one at a time.
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            try:
                self._model = WhisperModel(config.whisper_model, device="auto", compute_type="int8")
            except Exception as error:  # a GPU without the libraries Whisper needs
                print(f"Whisper couldn't use the GPU ({error}); using the CPU.", flush=True)
                self._model = WhisperModel(config.whisper_model, device="cpu", compute_type="int8")
        return self._model

    def warm_up(self) -> None:
        """Loads Whisper in the background so the first request isn't slow."""
        threading.Thread(target=self._load, daemon=True).start()

    def transcribe(self, audio: np.ndarray, vocabulary: str = "") -> str:
        # Anything shorter than a quarter second is a stray key press.
        if audio.size < config.sample_rate // 4:
            return ""
        # Greedy decoding (beam_size=1) is several times faster than the
        # default beam search and barely less accurate for short commands.
        with self._lock:
            segments, _ = self._load().transcribe(
                audio,
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
                # Names and terms from your dictionary, so Whisper spells them right.
                initial_prompt=f"Words that may come up: {vocabulary}." if vocabulary else None,
            )
            return " ".join(s.text.strip() for s in segments).strip()
