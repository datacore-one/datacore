# Voice Terminal: Open Source Landscape Research

**Date**: 2026-03-15
**Purpose**: Map the open-source voice assistant ecosystem to identify components to fork, build on, contribute to, or use.

---

## Tier 1: Directly Relevant to Our Architecture

These projects align closely with our brain+satellite architecture and could serve as foundations or major components.

### Xiaozhi ESP32 (78/xiaozhi-esp32)

- **URL**: https://github.com/78/xiaozhi-esp32
- **Stars**: 24,762 | **Forks**: 5,319
- **Language**: C++ | **License**: MIT
- **Last commit**: 2026-03-15 (very active)
- **Architecture**: ESP32 devices as voice entry points connecting to a backend server via MCP protocol. Server handles LLM inference. Supports wake word, VAD, STT streaming.
- **Server**: https://github.com/xinnan-tech/xiaozhi-esp32-server (8,875 stars, JS/Python)
- **What we could reuse**: ESP32 firmware for satellite nodes, MCP-based device-to-brain protocol, audio streaming architecture. Their client-server split mirrors our brain+ears design exactly.
- **Key differentiator**: Massive Chinese community, MCP protocol integration, explosive growth (24k stars). Most actively developed ESP32 voice project by far.
- **Relevance**: HIGH -- their architecture IS our architecture. Study their protocol and audio streaming approach.

### Willow (HeyWillow/willow)

- **URL**: https://github.com/HeyWillow/willow
- **Stars**: 2,990 | **Forks**: 117
- **Language**: C (ESP-IDF) | **License**: Apache-2.0
- **Last commit**: 2026-02-13 (active)
- **Architecture**: ESP32-S3-BOX as voice terminal with Willow Inference Server (WIS) as backend. Audio goes to WIS for STT, intent goes to Home Assistant or other platforms. AEC, AGC, noise suppression on device.
- **What we could reuse**: ESP32-S3 audio processing pipeline (AEC, AGC, noise suppression), inference server architecture, LCD/touch UI code for ESP32-S3-BOX-3.
- **Key differentiator**: < 500ms end-to-end latency. Production-quality audio processing. Commercially available $50 hardware.
- **Relevance**: HIGH -- best ESP32 voice terminal firmware. Their inference server concept maps to our Mac brain.

### Home Assistant Voice PE (esphome/home-assistant-voice-pe)

- **URL**: https://github.com/esphome/home-assistant-voice-pe
- **Stars**: 642 | **Forks**: 255
- **Language**: C++ (ESPHome) | **License**: CC
- **Last commit**: 2026-03-03 (active)
- **Architecture**: ESP32-S3 with 12-LED WS2812 ring, dual microphones, speaker. Runs ESPHome firmware, connects to Home Assistant as voice satellite.
- **What we could reuse**: LED ring state visualization code (listening/thinking/speaking animations), hardware design reference, ESPHome voice pipeline integration.
- **Key differentiator**: Official Home Assistant hardware. Best reference for LED ring feedback on ESP32. Open hardware design.
- **Relevance**: HIGH for LED/visual feedback on satellite hardware.

### Linux Voice Assistant (OHF-Voice/linux-voice-assistant)

- **URL**: https://github.com/OHF-Voice/linux-voice-assistant
- **Stars**: 323 | **Forks**: 54
- **Language**: Python | **License**: Apache-2.0
- **Last commit**: 2026-03-15 (very active)
- **Architecture**: Replaces deprecated wyoming-satellite. Linux device (Pi) as voice satellite using ESPHome protocol to connect to Home Assistant. Supports Pi Zero 2W + Satellite1 hat, ReSpeaker, or any PipeWire mic.
- **What we could reuse**: ESPHome protocol implementation for Linux satellites, audio pipeline for Pi-based nodes, hardware abstraction.
- **Key differentiator**: Official successor to wyoming-satellite. Active development by Home Assistant core team (synesthesiam/Mike Hansen).
- **Relevance**: HIGH -- if we want Pi-based satellites talking to our Mac brain, this is the reference implementation.

### FutureProofHomes Satellite1 (FutureProofHomes/Satellite1-ESPHome)

- **URL**: https://github.com/FutureProofHomes/Satellite1-ESPHome
- **Stars**: 127 | **Forks**: 59
- **Language**: C++ | **License**: TBD
- **Last commit**: 2026-03-13 (active)
- **Architecture**: ESP32-S3 + XMOS XU316 audio processor. 4-mic array, 25W amp, headphone jack, environmental sensors. ESPHome firmware.
- **What we could reuse**: Hardware design for a serious satellite node. XMOS audio processing (far superior to software AEC).
- **Key differentiator**: Professional audio quality with dedicated XMOS chip. Best open hardware design for a voice satellite.
- **Relevance**: MEDIUM-HIGH -- excellent hardware reference, but may be overkill for our Pi Zero prototype.

---

## Tier 2: Frameworks and Protocols

### Wyoming Protocol (OHF-Voice/wyoming)

- **URL**: https://github.com/OHF-Voice/wyoming (was rhasspy/wyoming)
- **Stars**: 341 | **Forks**: 43
- **Language**: Python | **License**: MIT
- **Last commit**: 2025-10 (maintained but ESPHome protocol is successor)
- **Architecture**: Simple peer-to-peer protocol for voice assistant components. JSON + audio streaming over TCP. Components: wake word, STT, intent, TTS each as separate services.
- **What we could reuse**: Protocol design for decomposing voice pipeline into microservices. Simple, elegant API design.
- **Key differentiator**: Pioneered the modular voice pipeline concept. Being replaced by ESPHome protocol for satellites but still used for STT/TTS services.
- **Relevance**: MEDIUM -- study the protocol design. Our pipeline already uses similar decomposition.
- **Note**: wyoming-satellite (1,231 stars) is archived/deprecated in favor of linux-voice-assistant.

### OVOS / Open Voice OS (OpenVoiceOS/ovos-core)

- **URL**: https://github.com/OpenVoiceOS/ovos-core
- **Stars**: 268 | **Forks**: 30
- **Language**: Python | **License**: Apache-2.0
- **Last commit**: 2026-03-14 (very active)
- **Architecture**: Modular voice assistant platform. Plugin-based STT/TTS/wake word. Skills framework (Mycroft-compatible). Message bus for IPC. Qt/QML GUI framework.
- **HiveMind satellite system**: https://github.com/JarbasHiveMind/HiveMind-core (14 stars, AGPL-3.0) -- distributed voice processing, satellites connect to central hub.
- **What we could reuse**: Plugin architecture patterns, skills framework concept, OVOS GUI (Qt/QML for touchscreen devices), HiveMind satellite protocol.
- **Key differentiator**: Most complete open-source voice assistant OS. Runs on Raspberry Pi with buildroot. Active EU-funded development (COALA/WASABI projects). Recently added macOS support.
- **Relevance**: MEDIUM -- too heavyweight for our needs but great reference. HiveMind satellite concept worth studying. Their ONNX STT work (phoonnx TTS) is cutting edge.

### Pipecat (pipecat-ai/pipecat)

- **URL**: https://github.com/pipecat-ai/pipecat
- **Stars**: 10,714 | **Forks**: 1,815
- **Language**: Python | **License**: BSD-2-Clause
- **Last commit**: 2026-03-15 (very active)
- **Architecture**: Framework for building real-time voice and multimodal conversational AI. Pipeline of processors (STT -> LLM -> TTS) with streaming. Supports 30+ AI services. WebRTC transport.
- **What we could reuse**: Pipeline architecture, streaming audio processing patterns, processor abstraction.
- **Key differentiator**: Best-in-class pipeline framework. Used by Daily.co in production. Supports both cloud and local models.
- **Relevance**: MEDIUM -- more suited for cloud voice agents. But their pipeline architecture is excellent reference for our local pipeline.

### LiveKit Agents (livekit/agents)

- **URL**: https://github.com/livekit/agents
- **Stars**: 9,719 | **Forks**: 2,912
- **Language**: Python | **License**: Apache-2.0
- **Last commit**: 2026-03-15 (very active)
- **Architecture**: Framework for building voice AI agents with WebRTC transport. STT -> LLM -> TTS pipeline with real-time streaming. Self-hostable.
- **What we could reuse**: Voice agent patterns, real-time audio processing, WebRTC for browser-based UI.
- **Key differentiator**: Production-grade WebRTC infrastructure. Can self-host.
- **Relevance**: MEDIUM -- useful if we want WebRTC-based satellite connections or browser UI.

---

## Tier 3: Visual Interface Components

### Vocalis (Lex-au/Vocalis)

- **URL**: https://github.com/Lex-au/Vocalis
- **Stars**: 292 | **Forks**: 55
- **Language**: TypeScript | **License**: Apache-2.0
- **Last commit**: 2025-04-14
- **Architecture**: Speech-to-speech assistant with "Assistant Orb" visual. State-aware animations (idle/listening/thinking/speaking). Mid-speech interruption. Uses OpenAI-compatible endpoints.
- **What we could reuse**: The visual orb concept and state animations. TypeScript/web-based UI approach.
- **Key differentiator**: Best implementation of a visual voice assistant orb with state feedback.
- **Relevance**: HIGH for visual interface. Fork their orb visualization.

### react-ai-orb (Steve0929/react-ai-orb)

- **URL**: https://github.com/Steve0929/react-ai-orb
- **Stars**: 41 | **Forks**: 6
- **Language**: TypeScript
- **Last commit**: 2025-02
- **Architecture**: React component for animated AI orb. Customizable glow, pulse, color.
- **What we could reuse**: Drop-in React component for our web UI.
- **Relevance**: MEDIUM -- small but directly usable component.

### voiceorb (aguscruiz/voiceorb)

- **URL**: https://github.com/aguscruiz/voiceorb
- **Stars**: 3 | **Forks**: 1
- **Language**: JavaScript
- **Architecture**: Four distinct visual states (Idle, Listening, Thinking, Speaking). Custom WebGL shaders. Perlin noise for organic displacement. Real-time audio reactivity.
- **What we could reuse**: Shader-based orb approach, state machine for visual feedback.
- **Relevance**: MEDIUM -- tiny project but the shader approach is exactly right. Worth studying the code.

### HuggingFace speech-to-speech (huggingface/speech-to-speech)

- **URL**: https://github.com/huggingface/speech-to-speech
- **Stars**: 4,549 | **Forks**: 522
- **Language**: Python | **License**: Apache-2.0
- **Last commit**: 2026-03-12 (active)
- **Architecture**: Modular pipeline: VAD -> STT -> LLM -> TTS. Supports Whisper, Parler-TTS, local LLMs. Gradio web UI included.
- **What we could reuse**: Pipeline orchestration patterns, Gradio UI for quick prototyping.
- **Key differentiator**: HuggingFace official. Great for prototyping with Gradio UI.
- **Relevance**: MEDIUM -- good reference implementation.

---

## Tier 4: TTS Alternatives to Evaluate

### Kokoro (hexgrad/kokoro)

- **URL**: https://github.com/hexgrad/kokoro
- **Stars**: 5,980 | **Forks**: 676
- **Language**: JavaScript | **License**: Apache-2.0
- **Last commit**: 2025-08
- **Architecture**: 82M parameter TTS model. 96x real-time on basic GPU. Apache-2.0 licensed weights.
- **FastAPI wrapper**: https://github.com/remsky/Kokoro-FastAPI (4,570 stars) -- production-ready API server with ONNX/GPU support.
- **What we could reuse**: Drop-in replacement for Piper. Higher quality, still fast. Apache license allows commercial use.
- **Key differentiator**: Best quality/speed ratio in open-source TTS. Beats Piper on quality while remaining fast. Top-ranked in TTS Arena (just behind ElevenLabs).
- **Relevance**: HIGH -- evaluate as Piper replacement for the Mac brain. Piper for Pi satellites (lower resource), Kokoro for Mac.

### RealtimeSTT (KoljaB/RealtimeSTT)

- **URL**: https://github.com/KoljaB/RealtimeSTT
- **Stars**: 9,553 | **Forks**: 824
- **Language**: Python | **License**: MIT
- **Last commit**: 2026-03-14 (very active)
- **Architecture**: Real-time STT library with VAD, wake word activation, instant transcription. Uses faster-whisper under the hood.
- **Companion**: RealtimeTTS (3,798 stars) -- streaming TTS with multiple engine support.
- **What we could reuse**: Already uses faster-whisper (our STT). Their VAD + wake word integration is more polished. Could replace our custom STT wrapper.
- **Key differentiator**: Best wrapper around faster-whisper with production-quality VAD and wake word.
- **Relevance**: HIGH -- could simplify our STT pipeline significantly.

### LocalAIVoiceChat (KoljaB/LocalAIVoiceChat)

- **URL**: https://github.com/KoljaB/LocalAIVoiceChat
- **Stars**: 714 | **Forks**: 75
- **Language**: Python
- **Last commit**: 2025-06
- **Architecture**: Full pipeline: RealtimeSTT + LLM (Zephyr 7B) + RealtimeTTS (Coqui XTTS). Custom voice cloning.
- **What we could reuse**: Integration patterns for RealtimeSTT + RealtimeTTS.
- **Relevance**: MEDIUM -- good reference for combining the Kolja libraries.

---

## Tier 5: Additional Notable Projects

### local-voice-ai (ShayneP/local-voice-ai)

- **URL**: https://github.com/ShayneP/local-voice-ai
- **Stars**: 453 | **Forks**: 144
- **Language**: TypeScript | **License**: MIT
- **Last commit**: 2026-03-14 (very active)
- **Architecture**: Ollama + Kokoro TTS + Nemotron STT + LiveKit. Next.js + Tailwind frontend UI.
- **What we could reuse**: Full-stack reference for local voice AI with web UI. Next.js frontend patterns.
- **Key differentiator**: Modern stack, actively maintained, has a visual UI.
- **Relevance**: MEDIUM -- good full-stack reference.

### Leon (leon-ai/leon)

- **URL**: https://github.com/leon-ai/leon
- **Stars**: 17,053 | **Forks**: 1,434
- **Language**: TypeScript | **License**: MIT
- **Last commit**: 2026-03-14 (active)
- **Architecture**: Skills-based personal assistant. Node.js core. Offline capable.
- **Relevance**: LOW -- different paradigm (skills-based NLU, not LLM-based).

### LocalAI (mudler/LocalAI)

- **URL**: https://github.com/mudler/LocalAI
- **Stars**: 43,660 | **Forks**: 3,703
- **Language**: Go | **License**: MIT
- **Last commit**: 2026-03-15 (very active)
- **Architecture**: OpenAI API drop-in replacement. Supports voice, vision, text. Runs on consumer hardware. P2P distributed inference.
- **What we could reuse**: OpenAI-compatible API server for local inference. Could serve as our LLM backend.
- **Relevance**: MEDIUM -- if we want to swap Claude API for local LLM, this is the backend.

### TEN Framework (TEN-framework/ten-framework)

- **URL**: https://github.com/TEN-framework/ten-framework
- **Stars**: 10,251 | **Forks**: 1,236
- **Language**: Python
- **Last commit**: 2026-03-14 (active)
- **Architecture**: Framework for conversational voice AI agents. Real-time multimodal.
- **Relevance**: LOW -- more suited for cloud agents.

### Rhasspy (rhasspy/rhasspy)

- **URL**: https://github.com/rhasspy/rhasspy
- **Stars**: 2,723 | **Forks**: 204 | **License**: MIT
- **Status**: ARCHIVED (2025-10). Superseded by Wyoming protocol components and Home Assistant voice.
- **Relevance**: HISTORICAL -- study for design patterns, but don't build on it.

---

## Recommendations

### Immediate Actions (for visual interface)

1. **Study Vocalis orb** -- their TypeScript orb visualization is exactly what we need for a web-based voice terminal UI. Fork the visual component.
2. **Study voiceorb shaders** -- WebGL shader approach for organic, reactive animations. Four-state model (idle/listening/thinking/speaking) maps to our pipeline.
3. **Prototype with Gradio** (from HuggingFace speech-to-speech) for quick iteration before building custom UI.

### Pipeline Improvements

4. **Evaluate RealtimeSTT** as replacement for our custom faster-whisper wrapper. It handles VAD + wake word + transcription in a single polished library.
5. **Evaluate Kokoro TTS** as upgrade from Piper on the Mac brain. Better quality, still fast, Apache-2.0 licensed. Keep Piper for resource-constrained satellites.

### Hardware Satellite

6. **Study Xiaozhi ESP32 protocol** -- their MCP-based device-to-server architecture is our architecture at 24k-star scale. Don't reinvent this.
7. **Study Willow** for ESP32-S3 audio processing (AEC, AGC, noise suppression).
8. **Study HA Voice PE** for LED ring animations on ESP32 satellites.
9. **Evaluate Satellite1 hardware** as a Pi-based satellite reference design.

### Architecture Decisions

10. **ESPHome protocol vs custom**: The HA ecosystem is moving to ESPHome protocol for voice satellites. Using it would give us Home Assistant compatibility for free. Worth considering vs rolling our own.
11. **HiveMind pattern**: OVOS HiveMind is the most mature satellite-to-brain protocol but is AGPL-licensed. Study the patterns, implement our own.

### Do NOT Build

- Do not build a full voice assistant OS (OVOS already exists)
- Do not build a new STT/TTS engine (use existing ones)
- Do not build a new wake word engine (OpenWakeWord is the standard)
- Do not build a new satellite protocol from scratch without studying Xiaozhi, Wyoming, and ESPHome first

---

## License Summary

| Project | License | Commercial OK? |
|---------|---------|----------------|
| Xiaozhi ESP32 | MIT | Yes |
| Willow | Apache-2.0 | Yes |
| Wyoming | MIT | Yes |
| OVOS Core | Apache-2.0 | Yes |
| HiveMind | AGPL-3.0 | No (copyleft) |
| Pipecat | BSD-2-Clause | Yes |
| LiveKit Agents | Apache-2.0 | Yes |
| Vocalis | Apache-2.0 | Yes |
| Kokoro | Apache-2.0 | Yes |
| RealtimeSTT | MIT | Yes |
| RealtimeTTS | MIT | Yes |
| HF speech-to-speech | Apache-2.0 | Yes |
| LocalAI | MIT | Yes |
| Linux Voice Asst | Apache-2.0 | Yes |
| Satellite1 | TBD | Check |
