#!/bin/sh
# Forced command for a narrowly scoped SSH key (authorized_keys `command=`):
# one OpenClaw model turn on the host's own subscription login, and nothing else.
# cross_model_audit.py's `openclaw-cli` route with `host:` sends
#     audit-infer <provider/model>
# with the prompt on stdin, and reads the JSON answer on stdout. `infer model run`
# is a plain model turn: the model gets no tools. Any other command is refused.
set -eu
set -f
# shellcheck disable=SC2086 -- split the client's command into words, no globbing
set -- ${SSH_ORIGINAL_COMMAND:-}
[ "$#" -eq 2 ] && [ "$1" = "audit-infer" ] || { echo "audit-infer: refused" >&2; exit 2; }
model="$2"
printf '%s' "$model" | grep -Eq '^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$' || { echo "audit-infer: bad model" >&2; exit 2; }
prompt="$(cat)"
exec openclaw infer model run --local --json --model "$model" --prompt "$prompt"
