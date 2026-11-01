"""Text-to-speech: turning an agent's reply into audio.

One small function, two backends, chosen by TTS_BACKEND in .env:

* edge   : Microsoft Edge neural voices. Needs internet. Works on Windows.
* phonon : Ollama's Phonon model through its OpenAI-compatible endpoint
           (POST /v1/audio/speech). Right now Ollama only supports it on Apple Silicon Macs,
           so point OLLAMA_HOST at a Mac, or wait for Windows/Linux support.
           (This path is written from Ollama's docs and has not been run on this machine.)

Each agent has its own voice, so you can HEAR which agent is talking.
Audio is streamed in chunks, so playback can start before the whole sentence is ready.
"""
from collections.abc import AsyncIterator

import httpx

from app.config import settings
from app.safety import speakable

# agent -> (Edge voice, Phonon voice)
AGENT_VOICES = {
    "triage": ("en-IN-NeerjaNeural", "freya"),
    "records": ("en-IN-PrabhatNeural", "sterling"),
    "scribe": ("en-US-GuyNeural", "toby"),
    "emergency": ("en-IN-NeerjaNeural", "harper"),
}


async def stream_speech(text: str, agent: str = "triage") -> AsyncIterator[bytes]:
    """Yield audio bytes for `text`. Numbers and abbreviations are spelled out first."""
    spoken = speakable(text)
    edge_voice, phonon_voice = AGENT_VOICES.get(agent, AGENT_VOICES["triage"])

    if settings.tts_backend == "phonon":
        async with httpx.AsyncClient(timeout=30) as client:
            body = {"model": "phonon", "input": spoken, "voice": phonon_voice}
            async with client.stream("POST", f"{settings.ollama_host}/v1/audio/speech", json=body) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk
        return

    import edge_tts  # imported here so the app still starts if the package is missing

    async for chunk in edge_tts.Communicate(spoken, edge_voice).stream():
        if chunk["type"] == "audio":
            yield chunk["data"]
