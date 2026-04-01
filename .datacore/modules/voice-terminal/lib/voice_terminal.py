#!/usr/bin/env python3
"""
Datacore Voice Terminal - Mac Prototype
Listens on Mac mic, detects wake word, transcribes, acts on Datacore.

Usage:
    python voice_terminal.py              # Full pipeline
    python voice_terminal.py --test-mic   # Test microphone
    python voice_terminal.py --test-stt   # Test STT with a recording
    python voice_terminal.py --test-tts "hello world"  # Test TTS
"""

import sys
import os
import time
import queue
import argparse
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

# ============================================================
# CONFIG
# ============================================================
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCKSIZE = 1280  # 80ms at 16kHz - what OpenWakeWord expects
SILENCE_THRESHOLD = 0.008  # RMS threshold for silence detection
SILENCE_DURATION = 1.5  # seconds of silence to stop recording
MAX_RECORD_SECONDS = 15
STT_MODEL = "medium"

DATACORE_ROOT = Path.home() / "Data"
INBOX_PATH = DATACORE_ROOT / "0-personal" / "org" / "inbox.org"


# ============================================================
# AUDIO DEVICE DETECTION
# ============================================================
_forced_device = None

def detect_input_device():
    """Find best input device. Prefer forced > external/USB > default."""
    if _forced_device is not None:
        d = sd.query_devices(_forced_device)
        print(f"Using forced input device [{_forced_device}]: {d['name']}")
        return _forced_device
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0:
            name = d['name'].lower()
            if 'macbook' in name:
                print(f"Using input device [{i}]: {d['name']}")
                return i
    default = sd.default.device[0]
    print(f"Using default input device [{default}]: {devices[default]['name']}")
    return default


# ============================================================
# WAKE WORD DETECTION
# ============================================================
class WakeWordDetector:
    def __init__(self):
        from openwakeword.model import Model
        print("Loading wake word model...")
        self.model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        print("Wake word ready. Say 'Hey Jarvis' to activate.")

    def process_audio(self, audio_chunk):
        """Process float32 audio chunk, return True if wake word detected."""
        audio_int16 = (audio_chunk * 32767).astype(np.int16)
        prediction = self.model.predict(audio_int16)
        for key in prediction:
            score = prediction[key]
            if score > 0.1:
                print(f"\r  wake: {score:.2f}", end="", flush=True)
            if score > 0.4:
                print(f"\n*** WAKE WORD DETECTED (confidence: {score:.2f}) ***")
                self.model.reset()
                return True
        return False

    def reset(self):
        """Full reset of wake word model state."""
        self.model.reset()


# ============================================================
# SPEECH TO TEXT
# ============================================================
class SpeechToText:
    def __init__(self, model_size=STT_MODEL):
        from faster_whisper import WhisperModel
        print(f"Loading Whisper {model_size} model...")
        self.model = WhisperModel(model_size, compute_type="int8")
        print("STT ready.")

    def transcribe(self, audio_data):
        """Transcribe float32 numpy array to text."""
        t0 = time.time()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
            with wave.open(f, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                audio_int16 = (audio_data * 32767).astype(np.int16)
                wf.writeframes(audio_int16.tobytes())
        # Try all three languages, pick highest log probability
        best_text = ""
        best_lang = "en"
        best_prob = -999

        for try_lang in ["en", "sl", "sr"]:
            segs, info = self.model.transcribe(tmp_path, language=try_lang)
            segs_list = list(segs)
            txt = " ".join(s.text.strip() for s in segs_list).strip()
            # Average log probability across segments
            if segs_list:
                avg_prob = sum(s.avg_logprob for s in segs_list) / len(segs_list)
            else:
                avg_prob = -999
            print(f"    {try_lang}: \"{txt}\" (prob: {avg_prob:.2f})")
            if avg_prob > best_prob and len(txt) > 1:
                best_prob = avg_prob
                best_text = txt
                best_lang = try_lang

        text = best_text
        lang = best_lang
        os.unlink(tmp_path)
        elapsed = time.time() - t0
        print(f"  STT ({elapsed:.1f}s, {lang}): \"{text}\"")
        return text, lang


# ============================================================
# TEXT TO SPEECH
# ============================================================
class TextToSpeech:
    def __init__(self):
        print("TTS ready (macOS 'say').")

    def speak(self, text):
        if not text:
            return
        print(f"  Speaking: \"{text}\"")
        subprocess.run(["say", "-r", "180", text], check=False)


# ============================================================
# INTENT ROUTER
# ============================================================
class IntentRouter:
    def route(self, text, lang="en"):
        t = text.lower().strip()

        # === SLOVENIAN / SERBIAN PATTERNS ===
        if lang in ("sl", "sr"):
            # "dodaj X" / "dodaj X na seznam" / "daj X na spisak"
            for prefix in ["dodaj ", "daj ", "postavi ", "zapiši "]:
                if t.startswith(prefix):
                    item = t[len(prefix):]
                    for suffix in ["na seznam", "na inbox", "na listo", "v inbox",
                                   "na nakupovalni seznam", "na seznam opravil",
                                   "na spisak", "na listu", "na popis"]:
                        if item.endswith(suffix):
                            item = item[:-len(suffix)].strip()
                            break
                    return ("add_inbox", {"item": item})

            # "kaj je naslednje" / "šta je sledeće" / "kaj imam za narest"
            if any(w in t for w in ["naslednje", "za narest", "naloge", "sledeće", "zadaci", "šta dalje"]):
                return ("query_next", {})

            # "kaj čakam" / "na šta čekam"
            if any(w in t for w in ["čakam", "čekam"]):
                return ("query_waiting", {})

            # "kaj vem o X" / "poišči X"
            if "kaj vem o" in t or "kaj vem za" in t:
                query = t.split("vem", 1)[1].strip()
                for prefix in ["o ", "za ", "o tem "]:
                    if query.startswith(prefix):
                        query = query[len(prefix):]
                return ("search_knowledge", {"query": query})

            if t.startswith("poišči ") or t.startswith("najdi "):
                query = t.split(" ", 1)[1].strip()
                return ("search_knowledge", {"query": query})

            if "stop" in t or "prekliči" in t or "nič" in t or "pozabi" in t:
                return ("cancel", {})

            return ("unknown", {"text": text})

        # === ENGLISH PATTERNS ===
        # Capture anything starting with "add" - route to inbox
        if t.startswith("add "):
            item = t[4:]
            for suffix in ["to my inbox", "to inbox", "to the grocery list", "to grocery list",
                           "to the list", "to my list", "to the shopping list", "to my to do list",
                           "to my to-do list", "to do list"]:
                if item.endswith(suffix):
                    item = item[:-len(suffix)].strip()
                    break
            return ("add_inbox", {"item": item})

        if "what" in t and "next" in t:
            return ("query_next", {})

        if "what" in t and "waiting" in t:
            return ("query_waiting", {})

        if t.startswith("what do i know about") or t.startswith("what do we know about"):
            query = t.split("about", 1)[1].strip() if "about" in t else ""
            return ("search_knowledge", {"query": query})

        if t.startswith("search for") or t.startswith("search "):
            query = t.replace("search for", "").replace("search", "").strip()
            return ("search_knowledge", {"query": query})

        if "stop" in t or "cancel" in t or "never mind" in t:
            return ("cancel", {})

        return ("unknown", {"text": text})


# ============================================================
# ACTIONS
# ============================================================
class DatacoreActions:
    def __init__(self):
        self.inbox_path = INBOX_PATH

    def execute(self, action, params):
        if action == "add_inbox":
            return self._add_to_inbox(params["item"])
        elif action == "query_next":
            return self._query_tasks("NEXT")
        elif action == "query_waiting":
            return self._query_tasks("WAITING")
        elif action == "search_knowledge":
            return f"Searching for {params.get('query', 'unknown')}. Knowledge search not wired up yet."
        elif action == "cancel":
            return "Cancelled."
        elif action == "unknown":
            return f"I heard: {params.get('text', '')}. I'm not sure what to do with that yet."
        return "Unknown action."

    def _add_to_inbox(self, item):
        try:
            from datetime import datetime
            now = datetime.now()
            date_str = now.strftime(f"[{now.year}-{now.month:02d}-{now.day:02d} {now.strftime('%a')}]")
            entry = f"\n* TODO {item.capitalize()}\n:PROPERTIES:\n:CREATED: {date_str}\n:SOURCE: voice-terminal\n:END:\n"
            with open(self.inbox_path, "a") as f:
                f.write(entry)
            print(f"  Added to inbox: {item}")
            return f"Added {item} to inbox."
        except Exception as e:
            return f"Sorry, couldn't add to inbox. {e}"

    def _query_tasks(self, state):
        na_path = DATACORE_ROOT / "0-personal" / "org" / "next_actions.org"
        try:
            content = na_path.read_text()
            tasks = []
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.startswith(f"* {state} ") or stripped.startswith(f"** {state} "):
                    task = stripped.split(state, 1)[1].strip()
                    if " :" in task:
                        task = task[:task.rfind(" :")]
                    tasks.append(task)
            if not tasks:
                return f"No {state.lower()} tasks found."
            count = min(5, len(tasks))
            task_list = ". ".join(tasks[:count])
            return f"You have {len(tasks)} {state.lower()} tasks. Top {count}: {task_list}."
        except Exception as e:
            return f"Couldn't read tasks: {e}"


# ============================================================
# MAIN LOOP - Single stream architecture
# ============================================================
def main_loop():
    """
    Single InputStream that stays open the entire time.
    States: LISTENING (wake word), RECORDING (after wake word), PROCESSING.
    No double-stream conflicts.
    """
    print("\n=== Datacore Voice Terminal ===\n")

    input_dev = detect_input_device()
    detector = WakeWordDetector()
    stt = SpeechToText()
    tts = TextToSpeech()
    router = IntentRouter()
    actions = DatacoreActions()

    audio_queue = queue.Queue()

    # States
    STATE_WAKE = "wake"
    STATE_RECORD = "record"
    state = STATE_WAKE

    # Recording state
    recording = []
    silence_start = None
    record_start = None

    def mic_callback(indata, frames, time_info, status):
        audio_queue.put(indata.copy())

    print("\nListening for wake word...\n")

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32',
                        blocksize=BLOCKSIZE, callback=mic_callback, device=input_dev):
        while True:
            try:
                chunk = audio_queue.get()
                chunk_flat = chunk.flatten()

                if state == STATE_WAKE:
                    # Check for wake word
                    if detector.process_audio(chunk_flat):
                        # Drain the queue to avoid processing stale audio
                        while not audio_queue.empty():
                            audio_queue.get_nowait()

                        tts.speak("yes?")

                        # Drain after TTS (mic heard the "yes?") and reset
                        time.sleep(0.3)
                        while not audio_queue.empty():
                            audio_queue.get_nowait()
                        detector.reset()

                        # Switch to recording
                        state = STATE_RECORD
                        recording = []
                        silence_start = None
                        record_start = time.time()
                        print("  Listening... (speak now)")

                elif state == STATE_RECORD:
                    recording.append(chunk_flat)
                    rms = np.sqrt(np.mean(chunk_flat ** 2))
                    elapsed = time.time() - record_start

                    # Level indicator
                    bars = int(rms * 300)
                    print(f"\r  {'|' * min(bars, 50):<50} {elapsed:.1f}s", end="", flush=True)

                    # Silence detection
                    if rms < SILENCE_THRESHOLD:
                        if silence_start is None:
                            silence_start = time.time()
                        elif time.time() - silence_start > SILENCE_DURATION:
                            print(f"\n  Silence detected after {elapsed:.1f}s")
                            state = STATE_WAKE  # will process below
                    else:
                        silence_start = None

                    # Max duration
                    if elapsed > MAX_RECORD_SECONDS:
                        print(f"\n  Max recording time ({MAX_RECORD_SECONDS}s)")
                        state = STATE_WAKE

                    # Process if we switched back to wake
                    if state == STATE_WAKE and recording:
                        audio = np.concatenate(recording)

                        if len(audio) < SAMPLE_RATE * 0.3:
                            print("  Too short, ignoring.")
                            print("\nListening for wake word...\n")
                            recording = []
                            continue

                        # Transcribe
                        text, lang = stt.transcribe(audio)

                        if not text or len(text.strip()) < 2:
                            print("  Empty transcription, ignoring.")
                            print("\nListening for wake word...\n")
                            recording = []
                            continue

                        # Route and execute
                        action, params = router.route(text, lang)
                        print(f"  Intent: {action} {params}")
                        response = actions.execute(action, params)

                        # Speak response
                        tts.speak(response)

                        # Full reset: drain queue, reset wake word model
                        time.sleep(0.5)
                        while not audio_queue.empty():
                            audio_queue.get_nowait()
                        detector.reset()

                        recording = []
                        print("\nListening for wake word...\n")

            except KeyboardInterrupt:
                print("\n\nShutting down voice terminal.")
                break


# ============================================================
# TEST FUNCTIONS
# ============================================================
def test_mic():
    dev = detect_input_device()
    dev_info = sd.query_devices(dev)
    native_rate = int(dev_info['default_samplerate'])
    print(f"Testing {dev_info['name']} at {native_rate}Hz... speak for 3 seconds.")
    audio = sd.rec(int(3 * native_rate), samplerate=native_rate, channels=1, dtype='float32', device=dev)
    sd.wait()
    rms = np.sqrt(np.mean(audio ** 2))
    peak = np.max(np.abs(audio))
    print(f"RMS: {rms:.4f}, Peak: {peak:.4f}")
    if rms > 0.001:
        print("Microphone is working!")
    else:
        print("WARNING: Very low audio level. Check mic permissions.")


def test_stt():
    dev = detect_input_device()
    print("Recording 5 seconds for STT test... SPEAK NOW!")
    audio = sd.rec(int(5 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype='float32', device=dev)
    sd.wait()
    print("Transcribing...")
    stt_obj = SpeechToText()
    text, lang = stt_obj.transcribe(audio.flatten())
    print(f"Result ({lang}): \"{text}\"")


def test_tts(text):
    tts_obj = TextToSpeech()
    tts_obj.speak(text)


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    import queue as queue  # ensure available for main_loop

    parser = argparse.ArgumentParser(description="Datacore Voice Terminal")
    parser.add_argument("--test-mic", action="store_true", help="Test microphone")
    parser.add_argument("--test-stt", action="store_true", help="Test speech-to-text")
    parser.add_argument("--test-tts", type=str, help="Test text-to-speech")
    parser.add_argument("--device", type=int, help="Force input device index (use --test-mic to list)")
    args = parser.parse_args()

    # Override device detection if --device is set
    if args.device is not None:
        globals()['_forced_device'] = args.device

    if args.test_mic:
        test_mic()
    elif args.test_stt:
        test_stt()
    elif args.test_tts:
        test_tts(args.test_tts)
    else:
        main_loop()
