#!/usr/bin/env bash
# Retry NotebookLM audio-create for a notebook + download when ready.
# Usage: nlm_audio_retry.sh <notebook-id> <instructions> <output-mp3-path>
# Polls with exponential backoff up to 1 hour total.

set -euo pipefail

NLM="${NLM:-$HOME/go/bin/nlm}"
NOTEBOOK_ID="${1:?notebook id required}"
INSTRUCTIONS="${2:?instructions required}"
OUTPUT="${3:?output path required}"

MAX_ATTEMPTS=12
SLEEP=60   # 1 min between attempts (12 × 1 min = 12 min total; bump by user if needed)

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  echo "[$(date -Iseconds)] attempt $attempt/$MAX_ATTEMPTS — audio-create on $NOTEBOOK_ID"
  if out=$("$NLM" create-audio "$NOTEBOOK_ID" "$INSTRUCTIONS" 2>&1); then
    echo "create-audio accepted, waiting for generation..."
    # Poll for audio readiness via audio get
    sleep 30
    for try in $(seq 1 20); do
      if "$NLM" audio download "$NOTEBOOK_ID" "$OUTPUT" 2>/dev/null; then
        echo "[$(date -Iseconds)] downloaded → $OUTPUT"
        exit 0
      fi
      sleep 30
    done
    echo "audio-create succeeded but download not ready after 10 min — leaving notebook for manual download"
    exit 2
  fi
  echo "$out" | head -2
  if [[ "$out" != *"Unavailable"* && "$out" != *"unavailable"* ]]; then
    echo "non-transient error — aborting"
    echo "$out"
    exit 3
  fi
  sleep "$SLEEP"
done

echo "[$(date -Iseconds)] NLM still unavailable after $MAX_ATTEMPTS attempts — try again later"
exit 1
