# stt.py
# Speech-to-text for AURA: records from microphone, transcribes with faster-whisper.

import os
import threading

try:
    import numpy as np
    import sounddevice as sd
    _SD_AVAILABLE = True
except ImportError:
    _SD_AVAILABLE = False

_model = None
_model_lock = threading.Lock()
_SAMPLE_RATE = 16000  # Whisper expects 16 kHz mono


def available():
    """Return True if sounddevice and faster-whisper are installed."""
    if not _SD_AVAILABLE:
        return False
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def list_input_devices():
    """Return [(device_name, sd_index), ...] for all input-capable devices."""
    if not _SD_AVAILABLE:
        return []
    result = []
    try:
        for i, d in enumerate(sd.query_devices()):
            if d['max_input_channels'] > 0:
                result.append((d['name'], i))
    except Exception:
        pass
    return result


def resolve_device(device_name):
    """Return sd device index for device_name.
    If device_name is empty, return the first available input device index
    (ALSA has no guaranteed default input on Pi, so None would silently fail).
    """
    if not _SD_AVAILABLE:
        return None
    try:
        devices = list(sd.query_devices())
        if device_name:
            for i, d in enumerate(devices):
                if d['max_input_channels'] > 0 and d['name'] == device_name:
                    return i
        # No name given (or not found) — fall back to first real input device
        for i, d in enumerate(devices):
            if d['max_input_channels'] > 0:
                return i
    except Exception:
        pass
    return None


def _load_model(model_size="tiny"):
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            model_dir = os.path.expanduser("~/models/whisper")
            os.makedirs(model_dir, exist_ok=True)
            _model = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8",
                download_root=model_dir,
            )
    return _model


def transcribe(audio_data, model_size="tiny", language=None, vad_filter=True,
               initial_prompt=None):
    """
    Transcribe float32 mono audio at 16 kHz.
    Returns (text: str, detected_language: str).
    Downloads the model on first call (~40 MB for tiny).
    """
    model = _load_model(model_size)
    segments, info = model.transcribe(
        audio_data,
        beam_size=1,
        language=language,
        vad_filter=vad_filter,
        initial_prompt=initial_prompt,
        condition_on_previous_text=False,
    )
    text = " ".join(s.text.strip() for s in segments).strip()
    return text, info.language


# ---------------------------------------------------------------------------
# Wake-phrase helpers
# ---------------------------------------------------------------------------

def _contains_wake(text, wake_phrases):
    """
    Return True if text starts with (or begins very close to) a wake phrase.
    Allows up to 15 chars of leading noise so minor transcription artefacts
    (punctuation, filler) don't block detection.
    """
    t = text.lower().strip()
    for phrase in wake_phrases:
        idx = t.find(phrase)
        if 0 <= idx <= 15:
            return True
    return False


def _strip_wake(text, wake_phrases):
    """
    Remove the leading wake phrase from transcribed text, along with any
    trailing punctuation that immediately follows it (comma, period, etc.).
    Returns the remainder, stripped of leading/trailing whitespace.
    """
    t = text.strip()
    t_low = t.lower()
    for phrase in sorted(wake_phrases, key=len, reverse=True):  # longest first
        idx = t_low.find(phrase)
        if 0 <= idx <= 15:
            remainder = t[idx + len(phrase):]
            return remainder.lstrip(" ,.:!–—").strip()
    return t


# ---------------------------------------------------------------------------
# Wake phrase builder
# ---------------------------------------------------------------------------

def build_wake_phrases(assistant_name: str, prefix: str = None) -> list:
    """Build the list of wake phrases Vosk listens for.

    Default prefixes are 'hey', 'ok', and 'okay'.  A user-configured prefix
    replaces all three when set.
    """
    name = assistant_name.lower().strip()
    prefixes = [prefix.lower().strip()] if prefix else ["hey", "ok", "okay"]
    phrases = []
    for p in prefixes:
        phrases.append(f"{p} {name}")
        phrases.append(f"{p}, {name}")
    return phrases


# ---------------------------------------------------------------------------
# Always-on background listener (Stage 1: Vosk wake, Stage 2: Whisper transcribe)
# ---------------------------------------------------------------------------

class BackgroundListener:
    """
    Two-stage STT listener.

    Stage 1 — Vosk (always running, tiny footprint):
      Feeds audio through a constrained grammar of wake phrases only.
      On detection, mutes itself and signals Stage 2 to begin.

    Stage 2 — faster-whisper (triggered on demand):
      Records until SILENCE_NEEDED seconds of RMS < ENERGY_FLOOR, then
      transcribes, strips the wake phrase prefix, and fires on_transcript.

    Public interface:
        on_transcript  callable(str) — fired with stripped transcript text
        mute() / unmute()            — suppress detection during TTS playback
    """

    BLOCK_SIZE     = 8000   # samples per PortAudio block (0.5 s at 16 kHz)
    ENERGY_FLOOR   = 0.015  # RMS silence threshold
    SILENCE_NEEDED = 2.5    # consecutive silence seconds to end an utterance

    def __init__(self, on_transcript, assistant_name=None, wake_prefix=None):
        import db as _db
        self._on_transcript  = on_transcript
        self._muted          = False
        self._running        = False
        self._state          = "idle"   # idle | active
        self._active_buffer  = []
        self._silence_count  = 0

        mic_device         = _db.get("stt_microphone") or None
        self._device       = resolve_device(mic_device)
        self._model_size   = _db.get("stt_model") or "tiny"
        vosk_path          = _db.get("vosk_model_path") or \
                             "/home/aura/models/vosk/small-en-us"
        name               = assistant_name or _db.get("assistant_name") or "aura"
        prefix             = wake_prefix or _db.get("wake_prefix") or None

        self._wake_phrases       = build_wake_phrases(name, prefix)
        self._wake_phrases_lower = [p.lower() for p in self._wake_phrases]

        # Initialise Vosk
        import json as _json
        from vosk import Model, KaldiRecognizer
        self._vosk_model  = Model(vosk_path)
        grammar           = _json.dumps(self._wake_phrases + ["[unk]"])
        self._recogniser  = KaldiRecognizer(self._vosk_model, _SAMPLE_RATE, grammar)

        # Pre-load Whisper so the first transcription has no latency spike
        _load_model(self._model_size)

    # --- Public interface ---

    def start(self):
        self._running = True
        threading.Thread(
            target=self._listen_loop, daemon=True, name="aura-stt-vosk"
        ).start()

    def stop(self):
        self._running = False

    def mute(self):
        """Suppress wake detection — call before TTS playback."""
        self._muted = True
        self._state = "idle"
        self._active_buffer.clear()
        self._silence_count = 0

    def unmute(self):
        """Resume wake detection — call after TTS playback."""
        self._muted = False

    # --- Audio callback (PortAudio thread) ---

    def _audio_callback(self, indata, frames, time_info, status):
        if not self._muted:
            import queue as _q
            try:
                self._audio_queue.put_nowait(bytes(indata))
            except _q.Full:
                pass

    # --- Main listener loop ---

    def _listen_loop(self):
        import queue as _q
        import numpy as np
        self._audio_queue = _q.Queue(maxsize=400)

        print(f"[stt] Vosk wake listener starting — device={self._device} "
              f"phrases={self._wake_phrases}", flush=True)

        try:
            with sd.RawInputStream(
                samplerate=_SAMPLE_RATE,
                blocksize=self.BLOCK_SIZE,
                device=self._device,
                dtype="int16",
                channels=1,
                callback=self._audio_callback,
            ):
                print("[stt] mic stream open — Vosk listening for wake phrase", flush=True)
                while self._running:
                    try:
                        data = self._audio_queue.get(timeout=0.5)
                    except _q.Empty:
                        continue

                    if self._state == "idle":
                        self._process_idle(data)
                    else:
                        self._process_active(data)

        except Exception as e:
            print(f"[stt] stream error: {e}", flush=True)

    # --- Idle: Vosk wake detection ---

    def _process_idle(self, data):
        import json as _json
        if self._recogniser.AcceptWaveform(data):
            result = _json.loads(self._recogniser.Result())
            text   = result.get("text", "").lower().strip()
            if self._is_wake(text):
                self._on_wake_detected()
        else:
            partial = _json.loads(self._recogniser.PartialResult())
            text    = partial.get("partial", "").lower().strip()
            if self._is_wake(text):
                self._on_wake_detected()

    def _is_wake(self, text: str) -> bool:
        for phrase in self._wake_phrases_lower:
            if phrase in text:
                return True
        return False

    def _on_wake_detected(self):
        self._state         = "active"
        self._active_buffer = []
        self._silence_count = 0
        self._recogniser.Reset()
        print("[stt] wake detected — recording", flush=True)

    # --- Active: record until silence, then transcribe ---

    def _process_active(self, data):
        import numpy as np
        self._active_buffer.append(data)

        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        rms     = float(np.sqrt(np.mean(samples ** 2)))

        blocks_for_silence = int(
            self.SILENCE_NEEDED / (self.BLOCK_SIZE / _SAMPLE_RATE)
        )

        if rms < self.ENERGY_FLOOR:
            self._silence_count += 1
        else:
            self._silence_count = 0

        if self._silence_count >= blocks_for_silence:
            self._transcribe_and_fire()
            self._state = "idle"

    def _transcribe_and_fire(self):
        import numpy as np
        import db as _db
        if not self._active_buffer:
            return

        raw   = b"".join(self._active_buffer)
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        try:
            name = _db.get("assistant_name") or "aura"
            text, _ = transcribe(
                audio,
                model_size=self._model_size,
                language="en",
                initial_prompt=f"Hey {name},",
                vad_filter=True,
            )
            if not text:
                return
            text = _strip_wake(text, self._wake_phrases_lower)
            if text:
                print(f"[stt] transcript: {text!r}", flush=True)
                self._on_transcript(text)
        except Exception as e:
            print(f"[stt] transcription error: {e}", flush=True)
        finally:
            self._active_buffer = []
            self._silence_count = 0


# ---------------------------------------------------------------------------
# Manual push-to-talk recorder (kept for programmatic use)
# ---------------------------------------------------------------------------

class Recorder:
    """
    Toggle-style microphone recorder.
    Call start() to begin, stop() to end and retrieve float32 audio.
    """

    def __init__(self, device_name=None):
        self._device = resolve_device(device_name)
        self._chunks = []
        self._stream = None
        self._active = False

    def start(self):
        if not _SD_AVAILABLE:
            raise RuntimeError("sounddevice is not installed")
        self._chunks = []
        self._active = True
        self._stream = sd.InputStream(
            samplerate=_SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=self._device,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, time_info, status):
        if self._active:
            self._chunks.append(indata.copy())

    def stop(self):
        """Stop recording; return float32 mono array at 16 kHz (may be empty)."""
        self._active = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._chunks:
            import numpy as np
            return np.zeros(0, dtype="float32")
        import numpy as np
        return np.concatenate(self._chunks, axis=0).squeeze()

    @property
    def is_recording(self):
        return self._active
