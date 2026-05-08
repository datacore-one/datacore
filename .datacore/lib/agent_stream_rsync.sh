#!/usr/bin/env bash
# agent_stream_rsync.sh — pull the agent-stream JSONL from nightshift to
# the local Mac so the datacore-app daemon's JsonlAppendWatcher sees it.
#
# Wired by: ~/Library/LaunchAgents/com.datacore.agent-stream-rsync.plist
# (interval 30s; see deploy doc).
#
# Why rsync and not the relay's GET endpoint:
#   - Mac just needs the file. The watcher already fires on any
#     ~/.datacore/cos/agent-stream/events-*.jsonl change. No new
#     consumer code needed.
#   - rsync is cheap when nothing changed (one round-trip, no payload).
#   - Survives the relay being unreachable: just retries on next tick.
#
# Setup:
#   1. SSH key: this script assumes passwordless ssh to nightshift.
#      Test with: ssh nightshift true
#   2. Tailscale or LAN: `nightshift` should resolve to the right host.
#   3. The remote path is the user's actual home — adjust REMOTE_USER
#      if your deploy account isn't `deploy`.

set -euo pipefail

REMOTE_HOST="${AGENT_STREAM_REMOTE_HOST:-nightshift}"
REMOTE_USER="${AGENT_STREAM_REMOTE_USER:-deploy}"
REMOTE_PATH="${AGENT_STREAM_REMOTE_PATH:-<HOME>/.datacore/cos/agent-stream/}"
LOCAL_PATH="${AGENT_STREAM_LOCAL_PATH:-$HOME/.datacore/cos/agent-stream/}"

mkdir -p "$LOCAL_PATH"

# --update: skip files that are newer on the receiver. Protects local
# events the Mac itself wrote (chat sidecar, MCP, in-process agents)
# from being overwritten by the older remote copy.
#
# --inplace: rewrite the existing file in place rather than creating a
# new tempfile and renaming. Necessary because launchd's WatchPaths
# (and our daemon's filesystem watcher) trigger on the *real* file's
# mtime — atomic-rename swaps don't always fire on macOS.
#
# Note: with --inplace the watcher might see a partial line if it
# polls mid-rsync. The daemon's JsonlAppendWatcher is line-buffered
# and ignores malformed JSON, so this is a non-issue.
exec rsync -a --update --inplace \
  --include='events-*.jsonl' \
  --exclude='*' \
  "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}" "${LOCAL_PATH}"
