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
# Always-on background listener
# ---------------------------------------------------------------------------

class BackgroundListener:
    """
    Monitors audio continuously for a wake phrase.

    Phase 1 (idle):
      Accumulates IDLE_ACCUM seconds of audio.  If RMS > ENERGY_FLOOR the
      chunk is transcribed; if the wake phrase is found, switch to active.

    Phase 2 (active):
      Records everything.  Once SILENCE_NEEDED consecutive seconds of RMS <
      ENERGY_FLOOR are seen, transcribes the full buffer, strips the wake
      phrase, and fires on_transcript(text).  Returns to idle.

    All callbacks are invoked from the background thread.  Callers that
    update GTK widgets must wrap them in ``GLib.idle_add``.
    """

    BLOCK_SECS     = 0.5    # stream blocksize for responsive silence detection
    IDLE_ACCUM     = 2.0    # seconds to buffer per wake-phrase check window
    ENERGY_FLOOR   = 0.015  # RMS below this counts as silence
    SILENCE_NEEDED = 2.5    # consecutive silence seconds to end an utterance

    def __init__(self, wake_phrases, on_wake=None, on_transcript=None,
                 on_idle=None, on_ready=None, device_name=None, model_size="tiny"):
        self._wakes         = [w.lower() for w in wake_phrases]
        self._on_wake       = on_wake
        self._on_transcript = on_transcript
        self._on_idle       = on_idle
        self._on_ready      = on_ready
        self._device        = resolve_device(device_name)
        self._model_size    = model_size
        self._stop_evt      = threading.Event()
        self._state         = "loading"   # loading | idle | active
        self._muted         = False       # True while TTS is playing

    @property
    def state(self):
        return self._state

    def start(self):
        self._stop_evt.clear()
        self._state = "loading"
        threading.Thread(target=self._run, daemon=True, name="aura-stt-bg").start()

    def stop(self):
        self._stop_evt.set()

    def mute(self):
        """Suppress wake detection (call when TTS starts)."""
        self._muted = True

    def unmute(self):
        """Re-enable wake detection (call when TTS ends)."""
        self._muted = False
        self._state = "idle"   # discard any active buffer captured during TTS

    def _run(self):
        import numpy as np
        import queue as _queue
        import time

        # Pre-load the Whisper model before opening the mic stream so the
        # first real transcription call has no latency spike.
        try:
            _load_model(self._model_size)
        except Exception as e:
            print(f"[stt] model load failed: {e}", flush=True)
            self._state = "idle"
            return

        import collections as _collections

        block_samples      = int(self.BLOCK_SECS * _SAMPLE_RATE)
        idle_blocks_needed = max(1, int(self.IDLE_ACCUM / self.BLOCK_SECS))
        # How many new blocks before re-running idle detection (50% overlap).
        slide_blocks       = max(1, idle_blocks_needed // 2)

        # Whisper initial prompt: hearing the assistant name primes the model
        # to transcribe it correctly.
        _name = self._wakes[0].capitalize() if self._wakes else "Aura"
        _wake_prompt = f"Hey {_name},"

        # Callback puts audio into a bounded queue; the main loop drains it.
        # This prevents XRUNs — audio is never dropped while Whisper is busy.
        audio_q = _queue.Queue(maxsize=200)   # ~100 s of headroom

        def _audio_cb(indata, frames, time_info, status):
            try:
                audio_q.put_nowait(indata.squeeze().copy())
            except _queue.Full:
                pass   # drop oldest-style: skip this block rather than block the audio thread

        # Rolling window (deque) for idle detection — 50% overlap avoids
        # wake words being split at window boundaries.
        idle_ring      = _collections.deque(maxlen=idle_blocks_needed)
        idle_new_count = 0    # new blocks since last idle transcription
        active_buf     = []   # everything recorded during active phase
        silence_t      = None # monotonic timestamp when silence began

        print(f"[stt] opening mic device index {self._device} "
              f"(wake: {self._wakes})", flush=True)

        self._state = "idle"
        if self._on_ready:
            self._on_ready()

        try:
            with sd.InputStream(
                samplerate=_SAMPLE_RATE,
                channels=1,
                dtype="float32",
                device=self._device,
                blocksize=block_samples,
                callback=_audio_cb,
            ) as stream:
                print(f"[stt] mic stream open — listening", flush=True)
                while not self._stop_evt.is_set():
                    try:
                        chunk = audio_q.get(timeout=0.5)
                    except _queue.Empty:
                        continue
                    rms = float(np.sqrt(np.mean(chunk ** 2)))

                    # ── Idle: sliding window wake-phrase check ──────────────
                    if self._state == "idle":
                        idle_ring.append(chunk)
                        idle_new_count += 1

                        if self._muted:
                            continue

                        if (len(idle_ring) >= idle_blocks_needed
                                and idle_new_count >= slide_blocks):
                            idle_new_count = 0
                            window = np.concatenate(idle_ring)
                            w_rms  = float(np.sqrt(np.mean(window ** 2)))
                            if w_rms > self.ENERGY_FLOOR:
                                try:
                                    text, _ = transcribe(window,
                                                         model_size=self._model_size,
                                                         language="en",
                                                         initial_prompt=_wake_prompt)
                                except Exception as e:
                                    print(f"[stt] transcribe error: {e}", flush=True)
                                    text = ""
                                print(f"[stt] idle rms={w_rms:.4f} heard: {text!r}",
                                      flush=True)
                                if _contains_wake(text, self._wakes):
                                    print("[stt] wake phrase detected → active",
                                          flush=True)
                                    self._state = "active"
                                    active_buf  = list(idle_ring)
                                    idle_ring.clear()
                                    idle_new_count = 0
                                    # Drain any audio queued during inference so
                                    # it's included in the utterance buffer.
                                    while True:
                                        try:
                                            active_buf.append(audio_q.get_nowait())
                                        except _queue.Empty:
                                            break
                                    silence_t = None
                                    if self._on_wake:
                                        self._on_wake()
                            else:
                                print(f"[stt] idle rms={w_rms:.4f} (below floor, skipped)",
                                      flush=True)

                    # ── Active: record until SILENCE_NEEDED of quiet ────────
                    else:
                        active_buf.append(chunk)
                        if rms < self.ENERGY_FLOOR:
                            if silence_t is None:
                                silence_t = time.monotonic()
                            elif time.monotonic() - silence_t >= self.SILENCE_NEEDED:
                                # Enough silence — transcribe and send
                                full       = np.concatenate(active_buf)
                                active_buf = []
                                silence_t  = None
                                self._state = "idle"
                                if self._on_idle:
                                    self._on_idle()
                                try:
                                    text, _ = transcribe(full,
                                                         model_size=self._model_size,
                                                         language="en")
                                    text = _strip_wake(text, self._wakes)
                                except Exception as e:
                                    print(f"[stt] active transcribe error: {e}",
                                          flush=True)
                                    text = ""
                                print(f"[stt] sending transcript: {text!r}", flush=True)
                                if text.strip() and self._on_transcript:
                                    self._on_transcript(text.strip())
                        else:
                            silence_t = None   # speech detected — reset timer

        except Exception as e:
            print(f"[stt] BackgroundListener error: {e}", flush=True)

        self._state = "idle"
        if self._on_idle:
            self._on_idle()


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
