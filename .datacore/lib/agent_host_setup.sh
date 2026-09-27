#!/usr/bin/env bash
# Declare an agent host, or verify it: identity, the crons the job contracts
# assume, the artifacts they read. Idempotent; run it again after every change.
#
#   agent_host_setup.sh --host NAME            apply, then verify (NAME: a roster machine)
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
    --host) HOST="${2:?--host needs a machine name from .datacore/registry/infrastructure.yaml}"; shift ;;
    --verify) VERIFY_ONLY=1 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac; shift
done
[ -n "$HOST" ] || { echo "--host is required" >&2; exit 2; }
# The code tree each host runs. Satellites run from a separate runner checkout;
# the box (winston) has none and runs straight from ~/Data, so for it the
# "runner" is ~/Data itself -- the same refresh and drift check then apply.
if [ "$HOST" = box ]; then
  RUNNER="${DATACORE_RUNNER:-$HOME/Data}"
else
  RUNNER="${DATACORE_RUNNER:-$HOME/.datacore/v2-runner}"
fi
LIB="$RUNNER/.datacore/lib"
STATE="${DATACORE_STATE:-$HOME/.datacore/state}"
ID_FILE="$HOME/.datacore/identity.env"
log() { echo "[host-setup] $*"; }
fail=0
qgrep() { grep "$@" >/dev/null; }

# ── identity (DIP-0044) ──────────────────────────────────────────────────────
# The registry is a PRIVATE OVERLAY: it lives in the data root (~/Data), not in
# the runner checkout this script runs from. actor_identity already resolves
# where it really is (6e3e0e4); reuse that rather than a second lookup, which
# is how this line came to read a file that is never in the runner.
ACTOR="$(python3 - "$HOST" "$LIB" <<'PY'
import sys, yaml
host, lib = sys.argv[1], sys.argv[2]
sys.path.insert(0, lib)
from actor_identity import REGISTRY_DIR
reg = REGISTRY_DIR / "infrastructure.yaml"
d = yaml.safe_load(open(reg)) or {}
print(((d.get("servers") or {}).get(host) or {}).get("access", {}).get("actor", ""))
PY
)"
[ -n "$ACTOR" ] || { log "FAIL registry declares no actor for host $HOST"; exit 2; }
# WHAT KIND of agent host this is picks the crons and checks below -- not its
# name, so no host of ours is written here (INS-3). The roster says it
# (servers.<host>.setup_profile); a host whose identity file already declares
# the openclaw executor is an openclaw host; otherwise the profile is the
# host name itself (nightshift, hermes).
PROFILE="$(python3 - "$HOST" "$LIB" <<'PY'
import sys, yaml
host, lib = sys.argv[1], sys.argv[2]
sys.path.insert(0, lib)
from actor_identity import REGISTRY_DIR
try:
    d = yaml.safe_load(open(REGISTRY_DIR / "infrastructure.yaml")) or {}
except OSError:
    d = {}
print(((d.get("servers") or {}).get(host) or {}).get("setup_profile", ""))
PY
)"
if [ -z "$PROFILE" ]; then
  if grep -qsE '^(export )?DATACORE_EXECUTOR=openclaw' "$ID_FILE"; then PROFILE=openclaw; else PROFILE="$HOST"; fi
fi
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
  if [ "$PROFILE" = openclaw ] && ! grep -qsE '^(export )?DATACORE_EXECUTOR=' "$ID_FILE"; then
    printf '%s\n' "DATACORE_EXECUTOR=openclaw" >> "$ID_FILE"; log "executor declared: openclaw"
  fi
fi

# ── crons the contracts assume ──────────────────────────────────────────────
# One line per job, keyed on a marker substring; a stale line with the same
# marker is replaced, so a path change here reaches the crontab on the next run.
CRON_LINES=()
CRON_KEYS=()
case "$PROFILE" in
  nightshift)
    CRON_KEYS=(phase1-cycle bot-alive gate-check)
    CRON_LINES+=("25 * * * * DATACORE_ROOT=$HOME/Data $LIB/ledger_phase1_cycle.sh >> $STATE/phase1-cycle.log 2>&1")
    CRON_LINES+=("*/15 * * * * $LIB/unit_alive.sh datacore-telegram.service $STATE/${ACTOR}-bot.alive 2>>$STATE/${ACTOR}-bot.alive.err")
    CRON_LINES+=("40 8 * * * python3 $HOME/Data/.datacore/modules/nightshift/lib/gate_check.py >> $STATE/nightshift-gate.history 2>&1")
    ;;
  openclaw)
    CRON_KEYS=(phase1-cycle ledger-claim job-verify)
    # ONE clone per writer per host. Data attests X posts into ~/Data/2-plur-space
    # (DATACORE_ATTEST_SPACE) and the dispatcher used ~/spaces/5-plur: two copies
    # of the same writer log forked at seq 22 (found 2026-09-06). The dispatcher
    # now works in the same clone, and the hourly cycle converges it.
    CRON_LINES+=("25 * * * * DATACORE_ROOT=$HOME/Data $LIB/ledger_phase1_cycle.sh >> $STATE/phase1-cycle.log 2>&1")
    CRON_LINES+=("*/15 * * * * DISPATCH_SPACE=$HOME/Data/2-plur-space $LIB/ledger-claim-pull.sh >> $STATE/ledger-dispatch.log 2>&1")
    # plur-claw had contracts in the manifest and nothing running the verifier
    # (INS-7, 2026-09-26) -- the same gap hermes had until 2026-09-06.
    CRON_LINES+=("0 8 * * * JOB_VERIFY_RUNNER=$RUNNER DATACORE_ROOT=$HOME/Data python3 $LIB/job_verify.py --machine $HOST --manifest $LIB/jobs/manifest.yaml --alert log >> $STATE/job_verify.log 2>&1")
    ;;
  hermes)
    CRON_KEYS=(phase1-cycle job-verify)
    # Tris keeps a 5-plur clone at ~/Data/2-plur; the hourly cycle converges it
    # so its verifier attestations and cadence commits leave the host within the hour.
    CRON_LINES+=("25 * * * * DATACORE_ROOT=$HOME/Data $LIB/ledger_phase1_cycle.sh >> $STATE/phase1-cycle.log 2>&1")
    # hermes had contracts in the manifest and nothing running the verifier
    # (found 2026-09-06): its rows read "not heard from" by construction.
    # --manifest: hermes has no ~/Data/.datacore/lib; the runner copy is the canonical one (test_runner_manifest_matches_canonical).
    CRON_LINES+=("0 8 * * * JOB_VERIFY_RUNNER=$RUNNER DATACORE_ROOT=$HOME/Data python3 $LIB/job_verify.py --machine $HOST --manifest $LIB/jobs/manifest.yaml --alert log >> $STATE/job_verify.log 2>&1")
    ;;
esac

# Every host keeps its runner current. Nothing did on the satellites: on
# 2026-09-23 hermes's runner was 13 commits behind origin and plur-claw's 50,
# so fixes that "reached the fleet" were not running there. The mac refreshes
# its runner from sync_state_from_nightshift.sh; this is the same step for the
# hosts this installer owns. --ff-only: the runner is read-only by design, so a
# pull that cannot fast-forward is a problem to report, not to merge.
CRON_KEYS+=(runner-refresh)
CRON_LINES+=("17 * * * * git -C $RUNNER pull -q --ff-only origin main >> $STATE/runner-refresh.log 2>&1")
# Every host that runs Claude sessions keeps a copy of their transcripts:
# Claude Code prunes its own after a month, and nightshift (4,976) and the box
# (597) had no copy at all (MEM-67). Beside the host's state, not in a space.
CRON_KEYS+=(sync-traces)
CRON_LINES+=("10 0 * * * TRACES_DEST=$HOME/.datacore/traces/claude-code bash $LIB/sync_traces.sh >> $STATE/sync-traces.log 2>&1")

# ── slash commands the scheduled jobs invoke ────────────────────────────────
# A cron script that runs `claude -p "/weekly-plan ..."` needs that command
# PUBLISHED to Claude Code, which reads ~/.claude/commands/ and nothing else.
# Module commands live in .datacore/modules/<m>/commands/ and are not there by
# default: on winston that directory did not exist at all, so cos_weekly_plan.sh
# logged `Unknown command: /weekly-plan` and did nothing. It had never produced
# a weekly plan, and because the script reported success anyway, the contract
# that would have caught it was reading a file nothing ever wrote.
#
# Published under the qualified name `<module>:<command>.md`, as on the
# workstation -- 16 modules ship a `today-hook`, so bare names cannot be the
# rule. A bare alias is added ONLY when that name is unique across every module
# and no root command already claims it, because that is the name the scheduled
# scripts actually type.
if [ "$VERIFY_ONLY" = 0 ]; then
  mkdir -p "$HOME/.claude/commands"
  _mods="$HOME/Data/.datacore/modules"
  for _f in "$_mods"/*/commands/*.md; do
    [ -f "$_f" ] || continue
    _m=$(basename "$(dirname "$(dirname "$_f")")"); _c=$(basename "$_f" .md)
    ln -sfn "$_f" "$HOME/.claude/commands/$_m:$_c.md"
    # unique across modules, and not shadowing a root command?
    _n=$(ls "$_mods"/*/commands/"$_c".md 2>/dev/null | wc -l)
    if [ "$_n" -eq 1 ] && [ ! -e "$HOME/Data/.datacore/commands/$_c.md" ]; then
      ln -sfn "$_f" "$HOME/.claude/commands/$_c.md"
    fi
  done
  log "published $(ls "$HOME/.claude/commands" 2>/dev/null | wc -l) slash command(s)"
fi

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
case "$PROFILE" in
  openclaw) ensure_github_host_key ;;
  hermes)
    : # Tris's heartbeat is a systemd timer (tris-heartbeat.timer); no spaces to project here
    ;;
esac
# ── protections every machine gets (INS-7) ──────────────────────────────────
# A machine added to the fleet gets what the others have, from here, not by
# hand: the update guard and the git safety hooks were present on all four
# hosts and installed by no installer (OI-11), so the next machine would have
# had neither.
#
# Update guard: needrestart must not restart a unit mid-job (OPS-5).
GUARD_SRC="$RUNNER/.datacore/config/host/needrestart-datacore.conf"
GUARD_DST=/etc/needrestart/conf.d/datacore.conf
if [ "$VERIFY_ONLY" = 0 ] && [ -f "$GUARD_SRC" ] && ! cmp -s "$GUARD_SRC" "$GUARD_DST"; then
  sudo -n install -D -m 644 "$GUARD_SRC" "$GUARD_DST" && log "update guard installed ($GUARD_DST)" \
    || { log "FAIL could not install the update guard to $GUARD_DST (needs sudo)"; fail=1; }
fi
# Safety hooks: every checkout (the root and each space) runs Datacore's
# pre-commit through the central dispatcher. A checkout that already has a
# hook -- a hooksPath or its own .git/hooks/pre-commit -- is left as it is.
GITHOOKS="$RUNNER/.datacore/githooks"
has_hook() {  # has_hook DIR
  local hp; hp=$(git -C "$1" config core.hooksPath 2>/dev/null)
  { [ -n "$hp" ] && (cd "$1" && [ -x "$hp/pre-commit" ]); } || [ -e "$1/.git/hooks/pre-commit" ]
}
for _d in "$HOME/Data" "$HOME"/Data/[0-9]-*; do
  [ -e "$_d/.git" ] || continue
  has_hook "$_d" && continue
  if [ "$VERIFY_ONLY" = 0 ] && [ -x "$GITHOOKS/pre-commit" ]; then
    git -C "$_d" config core.hooksPath "$GITHOOKS" && log "safety hooks enabled for $(basename "$_d") (core.hooksPath)"
  fi
done

# lines this installer retires (superseded by one of the above)
RETIRE=("/usr/local/bin/ledger-pull-data.sh")

# Stable ownership keys and executable-aware legacy matching are shared with
# verification. A commented/stale invocation cannot satisfy the contract.
CRON_ARGS=(--state "$STATE/cron-recovery")
for ((i=0; i<${#CRON_LINES[@]}; i++)); do
  CRON_ARGS+=(--entry "${CRON_KEYS[$i]}" "${CRON_LINES[$i]}")
done
for r in "${RETIRE[@]}"; do CRON_ARGS+=(--retire "$r"); done
if [ "$VERIFY_ONLY" = 0 ]; then
  python3 "$LIB/cron_install.py" "${CRON_ARGS[@]}" || fail=1
fi

# ── verify ───────────────────────────────────────────────────────────────────
grep -qsE "^(export )?DATACORE_ACTOR=$ACTOR\$" "$ID_FILE" && log "OK  identity declared ($ACTOR)" || { log "FAIL identity not declared as $ACTOR in $ID_FILE"; fail=1; }
grep -qsE '^(export )?DATACORE_LEDGER_SIGN=1' "$ID_FILE" && log "OK  events signed (DATACORE_LEDGER_SIGN=1)" || { log "FAIL signing not declared in $ID_FILE"; fail=1; }
res="$(python3 "$LIB/actor_identity.py" 2>/dev/null)"; [ "${res%% *}" = "$ACTOR" ] && log "OK  resolver agrees: $res" || { log "FAIL resolver says '$res', registry says $ACTOR"; fail=1; }
python3 "$LIB/cron_install.py" --verify "${CRON_ARGS[@]}" || fail=1
if [ ! -f "$GUARD_SRC" ]; then
  log "WARN this runner ships no update guard ($GUARD_SRC) -- nothing to compare"
else
  cmp -s "$GUARD_SRC" "$GUARD_DST" && log "OK  update guard in place ($GUARD_DST)" || { log "FAIL update guard missing or stale: $GUARD_DST"; fail=1; }
fi
for _d in "$HOME/Data" "$HOME"/Data/[0-9]-*; do
  [ -e "$_d/.git" ] || continue
  has_hook "$_d" && log "OK  safety hooks: $(basename "$_d")" || { log "FAIL no pre-commit hook in $_d"; fail=1; }
done
[ -x "$LIB/ledger_phase1_cycle.sh" ] && log "OK  runner lib present at $LIB" || { log "FAIL runner lib missing: $LIB"; fail=1; }
# Current, not merely present: compare with origin/main as of the last fetch
# (the runner-refresh cron fetches hourly). Behind is a warning, not a failure:
# it clears on the next refresh, and failing here would block a re-run.
_behind=$(git -C "$RUNNER" rev-list --count HEAD..origin/main 2>/dev/null || echo "?")
if [ "$_behind" = "0" ]; then log "OK  runner current with origin/main"; else log "WARN runner is $_behind commit(s) behind origin/main (runner-refresh cron pulls hourly)"; fi
case "$PROFILE" in
  nightshift)
    systemctl show -p Environment --value nightshift-overnight.service 2>/dev/null | tr ' ' '\n' | qgrep -x "DATACORE_ACTOR=nightshift" && log "OK  overnight executor declares its own writer (nightshift)" || { log "FAIL overnight unit does not declare DATACORE_ACTOR=nightshift"; fail=1; }
    systemctl is-active --quiet datacore-telegram.service && log "OK  $ACTOR bot unit active" || { log "FAIL datacore-telegram.service not active"; fail=1; }
    systemctl is-active --quiet venture-heartbeat.service && log "OK  venture heartbeat active" || { log "FAIL venture-heartbeat.service not active"; fail=1; }
    ;;
  hermes)
    systemctl is-active --quiet "${ACTOR}-heartbeat.timer" && log "OK  ${ACTOR}-heartbeat.timer active" || { log "FAIL ${ACTOR}-heartbeat.timer not active"; fail=1; }
    systemctl --user is-active --quiet hermes-gateway.service 2>/dev/null && log "OK  hermes gateway (user unit) active" || { log "FAIL hermes-gateway.service (user) not active"; fail=1; }
    ;;
  openclaw)
    [ -d "$HOME/Data/2-plur-space/.git" ] && log "OK  dispatch space present ($HOME/Data/2-plur-space)" || { log "FAIL $HOME/Data/2-plur-space is not a repository"; fail=1; }
    grep -qsE '^(export )?DATACORE_EXECUTOR=openclaw' "$ID_FILE" && log "OK  executor declared: openclaw" || { log "FAIL executor not declared in $ID_FILE"; fail=1; }
    ;;
esac
[ "$fail" = 0 ] && log "ALL CHECKS PASS ($HOST)" || log "SOME CHECKS FAILED ($HOST)"
exit $fail
