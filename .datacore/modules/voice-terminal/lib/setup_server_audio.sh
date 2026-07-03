#!/usr/bin/env bash
# Setup script for speak_brief.py audio delivery on the nightshift server.
# Run once on the server: bash .datacore/modules/voice-terminal/lib/setup_server_audio.sh
#
# After running, tomorrow's 07:00 UTC /today briefing will deliver audio to Telegram.
# Tier 1 (Kokoro): requires model files in voice-terminal/models/ — not in git (~350MB).
# Tier 2 (gTTS): works immediately after this script, internet-only, no models needed.

set -euo pipefail

echo "=== speak_brief.py audio setup for nightshift server ==="
echo ""

# gTTS: cloud TTS fallback (Tier 2)
echo "Installing gTTS (Google Text-to-Speech fallback)..."
pip3 install --quiet gTTS
echo "  gTTS installed."
echo ""

# ffmpeg: needed for WAV/MP3 → OGG/Opus conversion (Telegram voice messages)
echo "Installing ffmpeg (for Telegram voice message OGG conversion)..."
if command -v apt-get &>/dev/null; then
    apt-get install -y --quiet ffmpeg
elif command -v yum &>/dev/null; then
    yum install -y --quiet ffmpeg
else
    echo "  WARNING: Could not detect package manager. Install ffmpeg manually."
    echo "  Without ffmpeg: audio is sent as MP3 (audio file, not voice message)."
fi
echo "  ffmpeg: $(ffmpeg -version 2>&1 | head -1 || echo 'not found')"
echo ""

# Verify Telegram credentials are in nightshift.env
NS_ENV="$HOME/config/nightshift.env"
if [ -f "$NS_ENV" ]; then
    if grep -q "TELEGRAM_BOT_TOKEN" "$NS_ENV" && grep -q "TELEGRAM_CHAT_ID" "$NS_ENV"; then
        echo "Telegram credentials: found in $NS_ENV"
    else
        echo "WARNING: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing from $NS_ENV"
        echo "  Add them to enable Telegram delivery:"
        echo "    TELEGRAM_BOT_TOKEN=your_token"
        echo "    TELEGRAM_CHAT_ID=your_chat_id"
    fi
else
    echo "WARNING: $NS_ENV not found — Telegram delivery will not work."
fi
echo ""

# Smoke test: dry-run on today's spoken.txt
DATA_DIR="${DATA_DIR:-$HOME/Data}"
TODAY=$(date +%Y-%m-%d)
SPOKEN="$DATA_DIR/0-personal/notes/journals/${TODAY}_spoken.txt"
SCRIPT="$DATA_DIR/.datacore/modules/voice-terminal/lib/speak_brief.py"

echo "=== Smoke test ==="
if [ -f "$SPOKEN" ]; then
    echo "Found spoken text: $SPOKEN"
    python3 "$SCRIPT" "$TODAY" --dry-run 2>&1 | head -5
    echo ""
    echo "Dry run OK. To send audio for today: python3 $SCRIPT $TODAY --telegram"
else
    echo "No spoken.txt found for $TODAY yet (runs at 07:00 UTC with /today)."
fi

echo ""
echo "=== Setup complete ==="
echo "Audio delivery tiers enabled:"
echo "  Tier 1 (Kokoro ONNX): model files NOT in git — download separately if wanted"
echo "     kokoro-v1.0.onnx (~310MB): https://github.com/thewh1teagle/kokoro-onnx/releases"
echo "     voices-v1.0.bin (~27MB):   same release"
echo "     Place in: $DATA_DIR/.datacore/modules/voice-terminal/models/"
echo "     Then: pip3 install kokoro-onnx soundfile"
echo "  Tier 2 (gTTS):   active now — cloud TTS, no models needed"
echo "  Tier 3 (sendAudio): fallback when OGG conversion fails (no ffmpeg)"
