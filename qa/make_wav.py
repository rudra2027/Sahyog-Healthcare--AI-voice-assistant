"""Makes the fake-microphone recording for the voice test: quiet, then a spoken sentence, then quiet again.
Run with the project's own Python:   .venv\\Scripts\\python qa/make_wav.py out.wav "sentence to speak"
"""
import asyncio
import sys
import tempfile
from pathlib import Path

import av
import edge_tts
import numpy as np

out_path, sentence = Path(sys.argv[1]), sys.argv[2]
mp3 = Path(tempfile.mkdtemp()) / "speech.mp3"
asyncio.run(edge_tts.Communicate(sentence, "en-IN-NeerjaNeural").save(str(mp3)))
pieces = []
with av.open(str(mp3)) as src:
    resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
    for frame in src.decode(audio=0):
        for piece in resampler.resample(frame):
            pieces.append(piece.to_ndarray().reshape(-1))
speech = np.concatenate(pieces)
audio = np.concatenate([np.zeros(48000 * 3 // 2, dtype=np.int16), speech, np.zeros(48000 * 4, dtype=np.int16)])
with av.open(str(out_path), "w", format="wav") as out:
    stream = out.add_stream("pcm_s16le", rate=48000, layout="mono")
    frame = av.AudioFrame.from_ndarray(audio.reshape(1, -1), format="s16", layout="mono")
    frame.sample_rate = 48000
    for packet in stream.encode(frame):
        out.mux(packet)
    for packet in stream.encode(None):
        out.mux(packet)
print("wrote", out_path)
