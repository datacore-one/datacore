#!/usr/bin/env bash
# The 2026-09-19 audit trio, as launchd runs them.
#
# WHY A WRAPPER AND NOT THREE PLISTS OF INLINE PYTHON. Each of these writes an
# artifact that a job contract asserts on, and the contract fails if the
# artifact is stale. That makes "did it run" as important as "did it pass", and
# a wrapper is the one place to guarantee both: the artifact is written on
# every path, including the failure paths, so a contract can tell "ran and
# found something" from "never ran at all".
#
# WHY LAUNCHD AND NOT CRON. This is a laptop. cron drops runs missed while the
# lid was shut; launchd catches up. The phase-1 cycle moved for the same reason
# on 2026-09-09 and its plist says so.
set -uo pipefail
LIB="${DATACORE_LIB:-$HOME/Data/.datacore/lib}"
STATE="${DATACORE_STATE:-$HOME/.datacore/state}"
PY="${DATACORE_PYTHON:-python3}"
mkdir -p "$STATE"

case "${1:-}" in
  invariants)
    "$PY" "$LIB/ledger_invariants.py" > "$STATE/ledger-invariants.log" 2>&1
    ;;
  config)
    "$PY" "$LIB/config_resolution_probe.py" > "$STATE/config-resolution.log" 2>&1
    ;;
  canary-run)
    "$PY" "$LIB/delegation_canary.py" --run --space "$HOME/Data/8-firm" \
      --assignee miles > "$STATE/delegation-canary.log" 2>&1
    ;;
  canary-check)
    "$PY" "$LIB/delegation_canary.py" --check --space "$HOME/Data/8-firm" \
      > "$STATE/delegation-canary-check.log" 2>&1
    ;;
  drill)
    # 27 delegation controls -- the claim race, a crash after claiming, a
    # dismissed item, a check that reaches outside the space it verifies -- in
    # about ten seconds, against a scratch_fleet root that is deleted after.
    # It was written, it passes, and until 2026-09-19 nothing ran it: no cron
    # entry, no launchd agent, no job contract, no CI job. The strongest
    # statement the system can make about delegation was only ever made when
    # somebody typed it by hand.
    "$PY" "$LIB/delegation_drill.py" > "$STATE/delegation-drill.log" 2>&1
    ;;
  drills)
    # The three isolated drills, in one job because they share a verdict: each
    # builds its own throwaway tree, none touches a real space, and any one of
    # them failing means the same thing.
    #
    #   ledger_chaos_drill   15 injected faults -- torn writes, a forked log, a
    #                        remote that black-holes at 192.0.2.1 (TEST-NET-1,
    #                        unroutable by RFC, so "push fails" is deterministic)
    #   phase1_drill         the generated-org flip and its reversal
    #   laptop_night_drill   a night of missed launchd windows on a closed lid
    #
    # NOT delegation_fleet_exercise: it says "real agents, real hosts, real
    # ledger" and seeds work that other machines actually execute, at real API
    # cost. That one is a human decision each time, not a cron entry.
    out="$STATE/drills.log"
    : > "$out"
    rc=0
    # name:expected-scenarios. The count is checked, not just the exit code,
    # because a drill that stopped collecting scenarios still exits 0 and still
    # says every control held -- true of three controls as much as fifteen.
    # "All of them passed" is not a claim until you know how many there were.
    one=$(mktemp -t datacore-drill)
    for spec in "ledger_chaos_drill:15 scenario" "phase1_drill:" "laptop_night_drill:8/8"; do
      d="${spec%%:*}"; want="${spec#*:}"
      echo "===== $d =====" >> "$out"
      # Each drill's output goes to its own file first, so a count check reads
      # ONLY that drill. Grepping the shared log would let one drill's numbers
      # satisfy another's check.
      if "$PY" "$LIB/$d.py" > "$one" 2>&1; then
        cat "$one" >> "$out"
        if [ -n "$want" ] && ! grep -qF "$want" "$one"; then
          echo "-- $d RAN BUT DID NOT REPORT '$want'" >> "$out"
          rc=1
        else
          echo "-- $d OK" >> "$out"
        fi
      else
        cat "$one" >> "$out"
        echo "-- $d FAILED" >> "$out"
        rc=1
      fi
    done
    rm -f "$one"
    if [ "$rc" = "0" ]; then
      echo "drills: 3/3 held" >> "$out"
    else
      echo "drills: FAILURES — see above" >> "$out"
    fi
    ( exit $rc )   # make the case branch's status the drills' verdict, since `rc=$?` below reads it
    ;;
  suites)
    # Every test suite, each in its own pytest process.
    #
    # WHY HERE AND NOT IN CI. `.gitignore` excludes `.datacore/modules/*/`, so a
    # checkout of the main repo contains no modules and CI cannot run their
    # tests at all. Each module is its own GitHub repo -- datacore-comms,
    # datacore-nightshift, and so on -- and as of 2026-09-19 not one of them has
    # a workflow. That is the whole reason 295 test files were gated by nothing:
    # not cost, not a decision, just no CI anywhere they live.
    #
    # A nightly run here covers all of them today, on the machine that has them.
    # Per-repo workflows are still the durable answer and would make this
    # redundant, which is the point.
    "$PY" "$LIB/suite_audit.py" --jobs 3 > "$STATE/suite-audit.log" 2>&1
    ;;
  *)
    echo "usage: audit_trio_run.sh {invariants|config|canary-run|canary-check|drill|drills|suites}" >&2
    exit 2
    ;;
esac
rc=$?
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) audit-trio ${1} rc=$rc" >> "$STATE/audit-trio.log"
exit $rc
