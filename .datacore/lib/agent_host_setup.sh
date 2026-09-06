#!/usr/bin/env bash
# Declare an agent host, or verify it: identity, the crons the job contracts
# assume, the artifacts they read. Idempotent; run it again after every change.
#
#   agent_host_setup.sh --host nightshift|hermes|plur-claw          apply, then verify
#   agent_host_setup.sh --host NAME --verify                        check, change nothing
#
# Why this exists: the box has had an installer with a verify step since
# 2026-09-03; the other three hosts were configured by hand, so a cron line
# lived only in a crontab, an identity only in a file someone once placed, and
# a broken dispatcher tick (plur-claw, since 2026-08-13) had nothing to
# compare itself against. The product description calls this stage 6:
# every host rebuildable from its installer.
set -uo pipefail
HOST=""; VERIFY_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="${2:?--host needs nightshift|hermes|plur-claw}"; shift ;;
    --verify) VERIFY_ONLY=1 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac; shift
done
[ -n "$HOST" ] || { echo "--host is required" >&2; exit 2; }
RUNNER="${DATACORE_RUNNER:-$HOME/.datacore/v2-runner}"
LIB="$RUNNER/.datacore/lib"
STATE="$HOME/.datacore/state"; mkdir -p "$STATE"
ID_FILE="$HOME/.datacore/identity.env"
log() { echo "[host-setup] $*"; }
fail=0
qgrep() { grep "$@" >/dev/null; }

# ── identity (DIP-0044) ──────────────────────────────────────────────────────
ACTOR="$(python3 - "$HOST" "$LIB/../registry/infrastructure.yaml" <<'PY'
import sys, yaml
host, reg = sys.argv[1], sys.argv[2]
d = yaml.safe_load(open(reg)) or {}
print(((d.get("servers") or {}).get(host) or {}).get("access", {}).get("actor", ""))
PY
)"
[ -n "$ACTOR" ] || { log "FAIL registry declares no actor for host $HOST"; exit 2; }
if [ "$VERIFY_ONLY" = 0 ]; then
  if ! grep -qsE '^(export )?DATACORE_ACTOR=' "$ID_FILE"; then
    mkdir -p "$(dirname "$ID_FILE")"
    printf '%s\n' "# DIP-0044: this machine writes the ledger as one declared actor. Registry: servers.$HOST.access.actor" "DATACORE_ACTOR=$ACTOR" >> "$ID_FILE"
    log "declared DATACORE_ACTOR=$ACTOR in $ID_FILE"
  fi
  # Stage 8: every event this writer appends is signed with its own key
  # (ledger/keys.py, opt-in switch in ledger/log.py). The key is generated on
  # first use under ~/.datacore/keys; the public half is registered in
  # .datacore/keys/registry.yaml so any host can verify the writer's chain.
  if ! grep -qsE '^(export )?DATACORE_LEDGER_SIGN=' "$ID_FILE"; then
    printf '%s\n' "DATACORE_LEDGER_SIGN=1" >> "$ID_FILE"; log "signing on for $ACTOR (DATACORE_LEDGER_SIGN=1)"
  fi
  mkdir -p "$HOME/.datacore/keys"; chmod 700 "$HOME/.datacore/keys"
  # The executor this host runs delegated items through (ledger_claim ->
  # executors/base.get_executor). plur-claw has no claude binary; it has openclaw.
  if [ "$HOST" = plur-claw ] && ! grep -qsE '^(export )?DATACORE_EXECUTOR=' "$ID_FILE"; then
    printf '%s\n' "DATACORE_EXECUTOR=openclaw" >> "$ID_FILE"; log "executor declared: openclaw"
  fi
fi

# ── crons the contracts assume ──────────────────────────────────────────────
# One line per job, keyed on a marker substring; a stale line with the same
# marker is replaced, so a path change here reaches the crontab on the next run.
CRON_LINES=()
case "$HOST" in
  nightshift)
    CRON_LINES+=("25 * * * * DATACORE_ROOT=$HOME/Data $LIB/ledger_phase1_cycle.sh >> $STATE/phase1-cycle.log 2>&1")
    CRON_LINES+=("*/15 * * * * $LIB/unit_alive.sh datacore-telegram.service $STATE/miles-bot.alive 2>>$STATE/miles-bot.alive.err")
    CRON_LINES+=("40 8 * * * python3 $HOME/Data/.datacore/modules/nightshift/lib/gate_check.py >> $STATE/nightshift-gate.history 2>&1")
    ;;
  plur-claw)
    # ONE clone per writer per host. Data attests X posts into ~/Data/2-plur-space
    # (DATACORE_ATTEST_SPACE) and the dispatcher used ~/spaces/5-plur: two copies
    # of the same writer log forked at seq 22 (found 2026-09-06). The dispatcher
    # now works in the same clone, and the hourly cycle converges it.
    CRON_LINES+=("25 * * * * DATACORE_ROOT=$HOME/Data $LIB/ledger_phase1_cycle.sh >> $STATE/phase1-cycle.log 2>&1")
    CRON_LINES+=("*/15 * * * * DISPATCH_SPACE=$HOME/Data/2-plur-space $LIB/ledger-claim-pull.sh >> $STATE/ledger-dispatch.log 2>&1")
    ;;
  hermes)
    # Tris keeps a 5-plur clone at ~/Data/2-plur; the hourly cycle converges it
    # so its verifier attestations and cadence commits leave the host within the hour.
    CRON_LINES+=("25 * * * * DATACORE_ROOT=$HOME/Data $LIB/ledger_phase1_cycle.sh >> $STATE/phase1-cycle.log 2>&1")
    # hermes had contracts in the manifest and nothing running the verifier
    # (found 2026-09-06): its rows read "not heard from" by construction.
    # --manifest: hermes has no ~/Data/.datacore/lib; the runner copy is the canonical one (test_runner_manifest_matches_canonical).
    CRON_LINES+=("0 8 * * * JOB_VERIFY_RUNNER=$RUNNER DATACORE_ROOT=$HOME/Data python3 $LIB/job_verify.py --machine hermes --manifest $LIB/jobs/manifest.yaml --alert log >> $STATE/job_verify.log 2>&1")
    ;;
esac
# ── known hosts the dispatch space needs (plur-claw fetches plur-space over ssh) ──
# The runner user's known_hosts was empty after the move from root's home to its own
# (DIP-0044 §3), so every fetch since 2026-08-13 failed "host key verification"
# and the dispatcher's pull error was swallowed. The key is added only when its
# fingerprint matches the one GitHub publishes; never blindly.
ensure_github_host_key() {
  local kh="$HOME/.ssh/known_hosts"; mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
  if ssh-keygen -F github.com -f "$kh" >/dev/null 2>&1; then log "OK  github.com in known_hosts"; return 0; fi
  [ "$VERIFY_ONLY" = 0 ] || { log "FAIL github.com not in known_hosts"; fail=1; return 0; }
  local scanned; scanned="$(ssh-keyscan -t ed25519 github.com 2>/dev/null | grep -v '^#')"
  local fp; fp="$(printf '%s\n' "$scanned" | ssh-keygen -lf - 2>/dev/null | awk '{print $2}')"
  if [ "$fp" = "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU" ]; then
    printf '%s\n' "$scanned" >> "$kh"; chmod 600 "$kh"; log "github.com host key added (fingerprint verified against GitHub's published ed25519 key)"
  else
    log "FAIL github.com host key fingerprint did not match GitHub's published key ($fp) — not added"; fail=1
  fi
}
case "$HOST" in
  plur-claw) ensure_github_host_key ;;
  hermes)
    : # Tris's heartbeat is a systemd timer (tris-heartbeat.timer); no spaces to project here
    ;;
esac
# lines this installer retires (superseded by one of the above)
RETIRE=("/usr/local/bin/ledger-pull-data.sh")

marker_of() { printf '%s' "$1" | sed -E 's/^[^ ]+ [^ ]+ [^ ]+ [^ ]+ [^ ]+ //' | cut -c1-60; }
if [ "$VERIFY_ONLY" = 0 ]; then
  cur="$(crontab -l 2>/dev/null || true)"
  new="$cur"
  for r in "${RETIRE[@]}"; do
    if printf '%s\n' "$new" | qgrep -F "$r"; then new="$(printf '%s\n' "$new" | grep -vF "$r")"; log "retired cron line: $r"; fi
  done
  for line in "${CRON_LINES[@]}"; do
    m="$(marker_of "$line")"
    if printf '%s\n' "$new" | qgrep -F "$m"; then
      new="$(printf '%s\n' "$new" | grep -vF "$m"; printf '%s\n' "$line")"
    else
      new="$(printf '%s\n' "$new"; printf '%s\n' "$line")"; log "cron added: $m"
    fi
  done
  printf '%s\n' "$new" | grep -vE '^\s*$' | crontab -
fi

# ── verify ───────────────────────────────────────────────────────────────────
grep -qsE "^(export )?DATACORE_ACTOR=$ACTOR\$" "$ID_FILE" && log "OK  identity declared ($ACTOR)" || { log "FAIL identity not declared as $ACTOR in $ID_FILE"; fail=1; }
grep -qsE '^(export )?DATACORE_LEDGER_SIGN=1' "$ID_FILE" && log "OK  events signed (DATACORE_LEDGER_SIGN=1)" || { log "FAIL signing not declared in $ID_FILE"; fail=1; }
res="$(python3 "$LIB/actor_identity.py" 2>/dev/null)"; [ "${res%% *}" = "$ACTOR" ] && log "OK  resolver agrees: $res" || { log "FAIL resolver says '$res', registry says $ACTOR"; fail=1; }
cur="$(crontab -l 2>/dev/null || true)"
for line in "${CRON_LINES[@]}"; do
  m="$(marker_of "$line")"
  printf '%s\n' "$cur" | qgrep -F "$m" && log "OK  cron: $m" || { log "FAIL cron missing: $m"; fail=1; }
done
for r in "${RETIRE[@]}"; do printf '%s\n' "$cur" | qgrep -F "$r" && { log "FAIL retired cron still present: $r"; fail=1; }; done
[ -x "$LIB/ledger_phase1_cycle.sh" ] && log "OK  runner lib present at $LIB" || { log "FAIL runner lib missing: $LIB"; fail=1; }
case "$HOST" in
  nightshift)
    systemctl show -p Environment --value nightshift-overnight.service 2>/dev/null | tr ' ' '\n' | qgrep -x "DATACORE_ACTOR=nightshift" && log "OK  overnight executor declares its own writer (nightshift)" || { log "FAIL overnight unit does not declare DATACORE_ACTOR=nightshift"; fail=1; }
    systemctl is-active --quiet datacore-telegram.service && log "OK  Miles bot unit active" || { log "FAIL datacore-telegram.service not active"; fail=1; }
    systemctl is-active --quiet venture-heartbeat.service && log "OK  venture heartbeat active" || { log "FAIL venture-heartbeat.service not active"; fail=1; }
    ;;
  hermes)
    systemctl is-active --quiet tris-heartbeat.timer && log "OK  tris-heartbeat.timer active" || { log "FAIL tris-heartbeat.timer not active"; fail=1; }
    systemctl --user is-active --quiet hermes-gateway.service 2>/dev/null && log "OK  hermes gateway (user unit) active" || { log "FAIL hermes-gateway.service (user) not active"; fail=1; }
    ;;
  plur-claw)
    [ -d "$HOME/Data/2-plur-space/.git" ] && log "OK  dispatch space present ($HOME/Data/2-plur-space)" || { log "FAIL $HOME/Data/2-plur-space is not a repository"; fail=1; }
    grep -qsE '^(export )?DATACORE_EXECUTOR=openclaw' "$ID_FILE" && log "OK  executor declared: openclaw" || { log "FAIL executor not declared in $ID_FILE"; fail=1; }
    ;;
esac
[ "$fail" = 0 ] && log "ALL CHECKS PASS ($HOST)" || log "SOME CHECKS FAILED ($HOST)"
exit $fail
