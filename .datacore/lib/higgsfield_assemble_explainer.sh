#!/bin/bash
# higgsfield_assemble_explainer.sh
#
# Locally assemble a Higgsfield video-explainer from per-block clips and
# narration takes, for when the server-side `explainer_video` assembler is not
# exposed on the account (checked 2026-08-05: absent from both
# `higgsfield model list` and `higgsfield workflow list` on a "plus" plan).
#
# Mirrors the documented server behaviour:
#   - every block is exactly BLOCK_SEC long
#   - the narration take is CENTRED inside its block, never time-stretched
#   - the clip's own ambient audio is ducked underneath the narration
#   - blocks are concatenated in order
#
# Layout expected in <dir>:
#   v1.mp4 v2.mp4 ... vN.mp4    one 10s clip per block, in order
#   a1.wav a2.wav ... aN.wav    one narration take per block, same order
#
# Usage:
#   higgsfield_assemble_explainer.sh <dir> [output.mp4] [block_seconds]
#
# ffmpeg resolution order: $FFMPEG env var, ffmpeg-static under <dir>, then PATH.
# A Homebrew ffmpeg from the third-party homebrew-ffmpeg tap may be broken by
# `brew autoremove` stripping its dylibs; `npm i ffmpeg-static` inside <dir>
# gives a self-contained binary without touching the system install.

set -euo pipefail

# Force C locale: under a comma-decimal locale (sl_SI, de_DE, …) awk parses
# "09.22" as 9 and prints "9,000000", which silently mistimes every block.
export LC_ALL=C LANG=C

DIR="${1:?usage: $0 <dir> [output.mp4] [block_seconds]}"
OUT="${2:-explainer.mp4}"
BLOCK_SEC="${3:-10}"
AMBIENT_GAIN="${AMBIENT_GAIN:-0.12}"   # ~ -18 dB beneath the narration

cd "$DIR"

if [ -n "${FFMPEG:-}" ]; then
  FF="$FFMPEG"
elif [ -x "./node_modules/ffmpeg-static/ffmpeg" ]; then
  FF="./node_modules/ffmpeg-static/ffmpeg"
elif command -v ffmpeg >/dev/null 2>&1 && ffmpeg -hide_banner -version >/dev/null 2>&1; then
  FF="ffmpeg"
else
  echo "error: no working ffmpeg. Run 'npm i ffmpeg-static' in $DIR" >&2
  exit 1
fi

# Count only numbered block clips. The glob must not match the output file —
# `v*.mp4` would happily swallow an output named e.g. verify.mp4 and then look
# for a block that does not exist.
N=$(ls 2>/dev/null | grep -cE '^v[0-9]+\.mp4$' || true)
[ "$N" -ge 2 ] || { echo "error: need at least 2 numbered blocks (v1.mp4, v2.mp4, …), found $N" >&2; exit 1; }

# `ffmpeg -i <file>` with no output always exits non-zero, so swallow its status
# explicitly — under `set -o pipefail` it would otherwise abort the script.
audio_duration() {
  local info
  info=$("$FF" -hide_banner -i "$1" 2>&1 || true)
  printf '%s\n' "$info" \
    | awk -F'[:,]' '/Duration:/ {printf "%.6f", ($2*3600)+($3*60)+$4; exit}'
}

: > concat.txt
for i in $(seq 1 "$N"); do
  [ -f "v${i}.mp4" ] && [ -f "a${i}.wav" ] || {
    echo "error: missing v${i}.mp4 or a${i}.wav" >&2; exit 1; }

  d=$(audio_duration "a${i}.wav")
  over=$(python3 -c "print(1 if ${d} > ${BLOCK_SEC} else 0)")
  if [ "$over" = "1" ]; then
    echo "warning: block ${i} narration is ${d}s, longer than ${BLOCK_SEC}s — it will be cut." >&2
    echo "         shorten the line or lower its punctuation density and regenerate." >&2
  fi

  pad_ms=$(python3 -c "print(max(0, int(round((${BLOCK_SEC}-${d})/2*1000))))")
  echo "block ${i}: narration ${d}s, centred with ${pad_ms}ms lead-in"

  "$FF" -y -hide_banner -loglevel error \
    -i "v${i}.mp4" -i "a${i}.wav" \
    -filter_complex "\
[0:a]volume=${AMBIENT_GAIN},atrim=0:${BLOCK_SEC},asetpts=N/SR/TB[amb];\
[1:a]adelay=${pad_ms}|${pad_ms},apad,atrim=0:${BLOCK_SEC},asetpts=N/SR/TB[nar];\
[amb][nar]amix=inputs=2:duration=first:normalize=0,alimiter=level_in=1:level_out=0.95[aout]" \
    -map 0:v -map "[aout]" \
    -t "$BLOCK_SEC" \
    -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -r 24 \
    -c:a aac -b:a 192k -ar 48000 \
    "block${i}.mp4"

  echo "file 'block${i}.mp4'" >> concat.txt
done

"$FF" -y -hide_banner -loglevel error -f concat -safe 0 -i concat.txt -c copy "$OUT"

echo "--- $OUT ---"
{ "$FF" -hide_banner -i "$OUT" 2>&1 || true; } | grep -E "Duration|Stream"
