"""The fleet week simulator's stand-in for Kokoro (the morning audio's local
text-to-speech, a 325 MB model the sandbox does not carry). Same interface:
`Kokoro(model, voices).create(text, voice=, speed=)` -> (samples, rate). It
returns a short silence per chunk, so the real audio path around it --
winston_speak.py, speak_brief.py, ffmpeg, the Telegram upload -- runs as on
the host. On the sandbox's PYTHONPATH only (fleet_week_sim.py)."""
import numpy as np


class Kokoro:
    def __init__(self, model_path, voices_path):
        self.model_path, self.voices_path = model_path, voices_path

    def create(self, text, voice="af_heart", speed=1.0, lang="en-us"):
        rate = 24000
        return np.zeros(max(1, int(rate * 0.01 * min(len(text or ""), 400))), dtype=np.float32), rate
