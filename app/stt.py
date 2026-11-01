"""Speech to text.

The browser records the person's voice and uploads it to POST /api/stt. We turn it into text with:

    1. Gemini 2.5 Flash (Google), when GOOGLE_API_KEY is set: fast and good with accents.  <- default
    2. faster-whisper, on this machine, as the fallback: slower, but needs no internet after the first download.

Both give the same thing back: plain text. If Gemini fails (no internet, key problem) we quietly use whisper.
Privacy note: with Gemini the voice clip is sent to Google. Set STT_BACKEND=whisper to keep audio on this machine.
"""
import base64
import io
import logging
import threading

import httpx
import numpy as np

from app.config import settings

log = logging.getLogger("sahyog")
_model = None
_lock = threading.Lock()

# A short hint makes the model spell hospital words correctly.
HINT = "Hospital appointment. Symptoms, doctor, report, cholesterol, thyroid, Cardiology, Orthopaedics, Baner, Banjara Hills."
GEMINI_PROMPT = (
    "Transcribe the speech in this audio into English text. The speaker may have an Indian accent and may use Hindi or "
    "Marathi words: translate those into English. Medical context: " + HINT + " "
    "Reply with ONLY the spoken words, no quotes or commentary. If there is no clear speech, reply with exactly: [silence]")


class SttUnavailable(Exception):
    """No speech engine is available. The browser then falls back to its own speech service."""


def backend() -> str:
    """'gemini', 'whisper' or 'browser'. 'auto' means Gemini when a key is present, else whisper."""
    choice = settings.stt_backend
    if choice == "auto":
        return "gemini" if settings.google_api_key else "whisper"
    return choice


def enabled() -> bool:
    return backend() in ("gemini", "whisper")


# ----------------------------------------------------------------------------
# Gemini
# ----------------------------------------------------------------------------
def to_wav(audio: bytes) -> bytes:
    """Any browser recording (webm/ogg/mp3...) -> 16 kHz mono WAV, which every speech service accepts."""
    import av   # installed together with faster-whisper

    out = io.BytesIO()
    with av.open(io.BytesIO(audio)) as source, av.open(out, "w", format="wav") as target:
        stream = target.add_stream("pcm_s16le", rate=16000, layout="mono")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in source.decode(audio=0):
            for piece in resampler.resample(frame):
                for packet in stream.encode(piece):
                    target.mux(packet)
        for packet in stream.encode(None):
            target.mux(packet)
    return out.getvalue()


def is_silent(wav: bytes) -> bool:
    """True when the clip is (almost) digital silence. Speech models sometimes invent a word like "Hello" for silence."""
    start = wav.find(b"data")                                    # the samples come after the "data" label and a 4-byte length
    samples = np.frombuffer(wav[start + 8:], dtype=np.int16) if start >= 0 else np.zeros(0, dtype=np.int16)
    return samples.size == 0 or int(np.abs(samples).max()) < 400


def transcribe_gemini(audio: bytes) -> str:
    wav = to_wav(audio)
    if is_silent(wav):
        return ""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
    body = {"contents": [{"parts": [{"text": GEMINI_PROMPT},
                                    {"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(wav).decode()}}]}],
            "generationConfig": {"temperature": 0, "thinkingConfig": {"thinkingBudget": 0}}}   # no "thinking": we want speed
    res = httpx.post(url, json=body, headers={"x-goog-api-key": settings.google_api_key}, timeout=20)
    res.raise_for_status()
    parts = res.json()["candidates"][0]["content"]["parts"]
    text = " ".join(p.get("text", "") for p in parts).strip().strip('"')
    return "" if text.lower().startswith("[silence") else text


# ----------------------------------------------------------------------------
# whisper (on this machine)
# ----------------------------------------------------------------------------
def load_model():
    """Load the model once (the first call downloads it). Thread-safe, because requests run in a thread pool."""
    global _model
    with _lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as err:
                raise SttUnavailable("faster-whisper is not installed") from err
            log.info("Loading speech model %s (first time downloads it)...", settings.whisper_model)
            _model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
            list(_model.transcribe(np.zeros(16000, dtype="float32"), language="en")[0])   # warm-up: the first real request is then fast
            log.info("Speech model ready")
    return _model


def transcribe_whisper(audio: bytes) -> str:
    segments, _info = load_model().transcribe(
        io.BytesIO(audio), language="en", beam_size=1, vad_filter=True,   # vad_filter skips silence, which also stops it "hearing" words in noise
        condition_on_previous_text=False, initial_prompt=HINT)
    return " ".join(seg.text.strip() for seg in segments if seg.no_speech_prob < 0.6).strip()


def transcribe(audio: bytes) -> str:
    """Audio bytes in, plain text out. Empty text means nobody was heard."""
    if not enabled():
        raise SttUnavailable("speech-to-text is turned off")
    if backend() == "gemini" and settings.google_api_key:
        try:
            return transcribe_gemini(audio)
        except Exception as err:   # no internet, bad key, quota...: do not lose the person's words
            log.warning("Gemini transcription failed (%s). Using whisper instead.", err)
    return transcribe_whisper(audio)
