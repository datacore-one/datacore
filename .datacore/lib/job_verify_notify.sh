#!/usr/bin/env bash
# Run job_verify and make sure a failure actually REACHES a human.
#
# Why this exists: job_verify's --alert telegram needs WINSTON_BOT_TOKEN /
# WINSTON_CHAT_ID in its environment. The workstation does not have them, so on
# that machine --alert telegram degrades to "logged only" -- the failure is
# detected and then dies in a local file nobody reads. Detected-but-undelivered
# is still silent, which is the whole failure class this tooling exists to
# close.
#
# Rather than copy the bot credentials onto a second machine (more copies of a
# secret, for no gain), this relays the failure text over the SSH channel that
# already exists to the always-on host, and lets THAT host -- which already
# holds the credentials -- do the sending.
#
# Usage:  job_verify_notify.sh --machine mac [--manifest PATH] [--relay HOST]
#
# Exit code is job_verify's own, so a scheduler still sees pass/fail.

set -uo pipefail

RELAY_HOST="${JOB_VERIFY_RELAY_HOST:-winston}"
RUNNER="${JOB_VERIFY_RUNNER:-$HOME/.datacore/v2-runner}"
LOG="${JOB_VERIFY_LOG:-$HOME/.datacore/state/job_verify.log}"
ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --relay) RELAY_HOST="$2"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

mkdir -p "$(dirname "$LOG")"

# --alert log, never telegram: this machine cannot send, and asking it to try
# only produces a misleading "telegram unavailable" line.
# Cron has a minimal PATH, so the interpreter is resolved explicitly rather
# than inherited. /usr/bin/python3 on macOS has no PyYAML -- using it makes
# every run fail with ModuleNotFoundError, which is a false alarm that looks
# exactly like a real one. Prefer an interpreter that can actually import the
# dependencies, and say so loudly if none can.
PY_BIN="${JOB_VERIFY_PYTHON:-}"
if [ -z "$PY_BIN" ]; then
  for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$HOME/.pyenv/shims/python3" python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import yaml' >/dev/null 2>&1; then
      PY_BIN="$c"; break
    fi
  done
fi
if [ -z "$PY_BIN" ]; then
  printf 'FATAL: no python3 with PyYAML found; job_verify cannot run\n' >> "$LOG"
  exit 2
fi

OUT="$(DATACORE_V2=1 DATACORE_ROOT="${DATACORE_ROOT:-$HOME/Data}" \
  "$PY_BIN" "$RUNNER/.datacore/lib/job_verify.py" "${ARGS[@]}" --alert log 2>&1)"
RC=$?

{ printf '=== %s ===\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"; printf '%s\n' "$OUT"; } >> "$LOG"

# Delivery. Two routes, because hosts differ in what they have:
#   relay  -- ssh to a host that holds the credentials (workstation: no creds
#             of its own, and copying secrets to a second machine buys nothing)
#   direct -- POST to Telegram using this host's own credentials (agent hosts:
#             they already have them, and cannot resolve the relay alias)
# Direct is tried when a credentials file is configured; relay otherwise.
# Whichever is used, a failure to DELIVER is written to the log rather than
# swallowed -- a broken alert path must not be quiet about being broken.
_deliver() {
  local msg="$1"
  if [ -n "${JOB_VERIFY_ENV_FILE:-}" ] && [ -r "${JOB_VERIFY_ENV_FILE}" ]; then
    # shellcheck disable=SC1090
    set -a; . "${JOB_VERIFY_ENV_FILE}"; set +a
    local tok="${TELEGRAM_BOT_TOKEN:-${WINSTON_BOT_TOKEN:-}}"
    local chat="${TELEGRAM_CHAT_ID:-${WINSTON_CHAT_ID:-}}"
    if [ -n "$tok" ] && [ -n "$chat" ]; then
      curl -s -m 15 -X POST "https://api.telegram.org/bot${tok}/sendMessage" \
        -d "chat_id=${chat}" --data-urlencode "text=${msg}" >/dev/null && return 0
      printf 'RELAY FAILED: direct telegram send failed\n' >> "$LOG"; return 1
    fi
    printf 'RELAY FAILED: %s has no bot token/chat id\n' "$JOB_VERIFY_ENV_FILE" >> "$LOG"; return 1
  fi
  # Paths are the RELAY HOST's, expanded by its shell: winston runs as a
  # normal user with Data under $HOME. The previous /root/Data and
  # /root/.datacore paths were a stale fact about a host that no longer runs
  # as root -- "Permission denied" on every relay since, so every alert from
  # this workstation was "logged only" and nobody was reading the log. Found
  # 2026-09-03. Since 2026-09-05 winston_send.py loads its own environment
  # (cos_env.py: cos.env -> .env -> local.env, later wins); WINSTON_BOT_TOKEN
  # lives only in local.env, so sourcing cos.env here would find nothing.
  printf '%s\n' "$msg" | ssh -o ConnectTimeout=15 -o BatchMode=yes "$RELAY_HOST" \
    'python3 ~/Data/.datacore/modules/chief-of-staff/server/lib/winston_send.py' \
    >>"$LOG" 2>&1 && return 0
  printf 'RELAY FAILED: could not deliver via %s\n' "$RELAY_HOST" >> "$LOG"; return 1
}

if [ "$RC" -ne 0 ] && [ -n "$OUT" ]; then
  # Relay to the host that holds the credentials. Failure to relay is itself
  # reported into the log rather than swallowed -- a broken alert path must not
  # be quiet about being broken.
  _deliver "$(printf 'job_verify FAILED on %s:\n%s' "$(hostname -s)" "$OUT")"
fi

exit "$RC"
