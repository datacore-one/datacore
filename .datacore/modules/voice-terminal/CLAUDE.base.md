---
summary: "Privacy-first voice assistant — wake word, STT, TTS, Datacore integration"
triggers: ["voice terminal", "voice assistant", "wake word", "speech to text"]
context: on_match
---

# Voice Terminal Module

## Purpose

Privacy-first voice assistant that runs speech processing locally. Mac prototype listens on microphone, detects wake word, transcribes via Whisper, and routes commands to Datacore. No cloud for audio.

## Quick Start
> Run `python voice_terminal.py` for the full pipeline, or `--test-mic` / `--test-stt` / `--test-tts` for component tests.

## How It Works

### Architecture
Brain+satellite model: Mac runs as the brain (STT, LLM, TTS), with potential ESP32 satellite nodes as voice entry points.

### Pipeline
```
Mic Input -> Wake Word Detection -> STT (Whisper) -> Datacore Action -> TTS Response
```

### Configuration
| Setting | Default | Purpose |
|---------|---------|---------|
| `wake_word` | `hey_datacore` | Activation phrase |
| `stt_model` | `medium` | Whisper model size |
| `tts_voice` | `en_US-lessac-medium` | Piper TTS voice |
| `brain` | `local` | `local` (Mac) or network (Blackpi IP) |

## Agents & Commands

None — early prototype stage. Voice commands route to existing Datacore agents.

## Key Paths

| Path | Purpose |
|------|---------|
| `lib/voice_terminal.py` | Mac prototype script |
| `lib/requirements.txt` | Python dependencies |
| `docs/landscape-research.md` | Open source ecosystem research |

## Setup

```bash
pip install -r lib/requirements.txt
```

Requires: `sounddevice`, `numpy`, Whisper model, Piper TTS.

## Boundaries
- Prototype stage — no agents or commands yet
- Audio never leaves the local machine
- ESP32 satellite support is planned, not implemented

---

*This file covers structure, capability, and stable configuration. Learned behavior, user corrections, and operational preferences live as engrams — call `plur_recall_hybrid` for those.*
