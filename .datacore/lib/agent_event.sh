#!/usr/bin/env bash
# agent_event.sh — DIP-0027 / DIP-0028 compliant emitter for shell-hook
# runtimes (Hermes hooks, simple cron jobs, openclaw pre-/post-hooks that
# happen to live in shell, etc.).
#
# Usage:
#   source agent_event.sh "<agent_id>" "<runtime>"
#   agent_event_tick
#   agent_event_task_completed "<task_id>" "<outcome>" "<tokens_used>"
#   agent_event_emit "<event_type>" '<json metadata>'
#
# Auto-discovers token + port from ~/.datacore/lens/auth/. Fail-soft:
# on capture failure, prints a warning to stderr and returns 0.
#
# Dependencies: bash 4+, curl, jq (jq optional — only needed for the
# typed wrappers; raw `agent_event_emit` works without it).

set -u

# Globals — set by source-time init below
AGENT_EVENT_AGENT_ID=""
AGENT_EVENT_RUNTIME=""
AGENT_EVENT_TOKEN=""
AGENT_EVENT_PORT="51717"
AGENT_EVENT_HOST="127.0.0.1"
AGENT_EVENT_TIMEOUT_S="2"

# Required-key spec mirrors DIP-0027 §8 / lens schema. Used only by the
# typed wrappers; the raw `agent_event_emit` does no client-side validation
# (server validates on receive).

agent_event_init() {
  local agent_id="${1:-}"
  local runtime="${2:-}"
  if [ -z "$agent_id" ] || [ -z "$runtime" ]; then
    echo "[agent_event] usage: agent_event_init <agent_id> <runtime>" >&2
    return 1
  fi
  case "$runtime" in
    claude-code|openclaw|hermes|nightshift|external) ;;
    *)
      echo "[agent_event] runtime '$runtime' not in {claude-code,openclaw,hermes,nightshift,external}" >&2
      return 1
      ;;
  esac
  AGENT_EVENT_AGENT_ID="$agent_id"
  AGENT_EVENT_RUNTIME="$runtime"

  local auth_dir="${HOME}/.datacore/lens/auth"
  if [ -r "${auth_dir}/token" ]; then
    AGENT_EVENT_TOKEN="$(< "${auth_dir}/token")"
    AGENT_EVENT_TOKEN="${AGENT_EVENT_TOKEN//[$'\t\r\n ']/}"
  fi
  if [ -r "${auth_dir}/port" ]; then
    local p
    p="$(< "${auth_dir}/port")"
    p="${p//[$'\t\r\n ']/}"
    if [[ "$p" =~ ^[0-9]+$ ]]; then
      AGENT_EVENT_PORT="$p"
    fi
  fi
  return 0
}

# Raw emit — caller provides event_type and a JSON metadata object string.
# Returns 0 always (fail-soft). Prints HTTP body to stdout if successful.
agent_event_emit() {
  local event_type="${1:-}"
  local metadata_json="${2:-{\}}"
  local target_id="${3:-}"

  if [ -z "$event_type" ]; then
    echo "[agent_event] event_type required" >&2
    return 0
  fi
  if [ -z "$AGENT_EVENT_TOKEN" ]; then
    echo "[agent_event] no token — capture skipped" >&2
    return 0
  fi

  # Inject agent_id + runtime into metadata. Use jq if available; otherwise
  # do string concatenation (fragile but works for simple cases).
  local final_metadata
  if command -v jq >/dev/null 2>&1; then
    final_metadata=$(printf '%s' "$metadata_json" | jq -c --arg aid "$AGENT_EVENT_AGENT_ID" --arg rt "$AGENT_EVENT_RUNTIME" '. + {agent_id: $aid, runtime: $rt}' 2>/dev/null) || final_metadata="$metadata_json"
  else
    # Best-effort splice: assumes metadata_json is a JSON object literal {...}
    if [[ "$metadata_json" == "{}" ]]; then
      final_metadata="{\"agent_id\":\"$AGENT_EVENT_AGENT_ID\",\"runtime\":\"$AGENT_EVENT_RUNTIME\"}"
    else
      final_metadata="${metadata_json%\}}, \"agent_id\":\"$AGENT_EVENT_AGENT_ID\", \"runtime\":\"$AGENT_EVENT_RUNTIME\"}"
    fi
  fi

  local target_field
  if [ -n "$target_id" ]; then
    target_field=", \"target_id\": \"${target_id}\""
  else
    target_field=", \"target_id\": null"
  fi

  local body
  body="{\"events\":[{\"event_type\":\"${event_type}\",\"actor\":\"agent\",\"surface\":\"agent\"${target_field},\"metadata\":${final_metadata}}]}"

  curl -fsS \
    --max-time "$AGENT_EVENT_TIMEOUT_S" \
    -X POST \
    -H "Authorization: Bearer ${AGENT_EVENT_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$body" \
    "http://${AGENT_EVENT_HOST}:${AGENT_EVENT_PORT}/api/lens/events" \
    2>/dev/null || {
      echo "[agent_event] capture failed (host ${AGENT_EVENT_HOST}:${AGENT_EVENT_PORT})" >&2
      return 0
    }
}

# --------------------------------------------- typed wrappers (jq required)

agent_event_tick() {
  agent_event_emit "agent.tick" "{}"
}

agent_event_session_started() {
  local session_id="${1:?session_id required}"
  agent_event_emit "agent.session_started" "{\"session_id\":\"${session_id}\"}"
}

agent_event_session_ended() {
  local session_id="${1:?session_id required}"
  local duration_ms="${2:-}"
  local outcome="${3:-}"
  local meta="{\"session_id\":\"${session_id}\""
  [ -n "$duration_ms" ] && meta="${meta},\"duration_ms\":${duration_ms}"
  [ -n "$outcome" ] && meta="${meta},\"outcome\":\"${outcome}\""
  meta="${meta}}"
  agent_event_emit "agent.session_ended" "$meta"
}

agent_event_task_received() {
  local task_id="${1:?task_id required}"
  local sender="${2:?sender required}"
  local trust_tier="${3:-}"
  local meta="{\"task_id\":\"${task_id}\",\"sender\":\"${sender}\""
  [ -n "$trust_tier" ] && meta="${meta},\"trust_tier\":\"${trust_tier}\""
  meta="${meta}}"
  agent_event_emit "agent.task_received" "$meta" "$task_id"
}

agent_event_task_claimed() {
  local task_id="${1:?task_id required}"
  agent_event_emit "agent.task_claimed" "{\"task_id\":\"${task_id}\"}" "$task_id"
}

agent_event_task_completed() {
  local task_id="${1:?task_id required}"
  local outcome="${2:?outcome required (success|partial|nochange)}"
  local tokens_used="${3:-}"
  case "$outcome" in
    success|partial|nochange) ;;
    *) echo "[agent_event] outcome '$outcome' not in {success,partial,nochange}" >&2; return 0 ;;
  esac
  local meta="{\"task_id\":\"${task_id}\",\"outcome\":\"${outcome}\""
  [ -n "$tokens_used" ] && meta="${meta},\"tokens_used\":${tokens_used}"
  meta="${meta}}"
  agent_event_emit "agent.task_completed" "$meta" "$task_id"
}

agent_event_task_failed() {
  local task_id="${1:?task_id required}"
  local error_class="${2:?error_class required}"
  agent_event_emit "agent.task_failed" \
    "{\"task_id\":\"${task_id}\",\"error_class\":\"${error_class}\"}" "$task_id"
}

agent_event_message_sent() {
  local recipient="${1:?recipient required}"
  local message_class="${2:?message_class required}"
  local message_id="${3:-}"
  agent_event_emit "agent.message_sent" \
    "{\"recipient\":\"${recipient}\",\"message_class\":\"${message_class}\"}" "$message_id"
}

agent_event_error() {
  local error_class="${1:?error_class required}"
  agent_event_emit "agent.error" "{\"error_class\":\"${error_class}\"}"
}

# Auto-init when sourced with two args:
if [ -n "${1:-}" ] && [ -n "${2:-}" ]; then
  agent_event_init "$1" "$2" || true
fi
