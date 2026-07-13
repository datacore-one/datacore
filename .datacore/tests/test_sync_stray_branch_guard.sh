#!/usr/bin/env bash
# Verify the abort_if_stray_branch guard added to ~/Data/sync.
#
# Three cases:
#   main branch      -> push proceeds
#   stray branch     -> push ABORTS and alerts
#   stray + override -> push proceeds (deliberate escape hatch)
ALERT_FILE=/tmp/sync-guard-test-alert.log
rm -f "$ALERT_FILE"

sync_alert() {
    local msg="[test] SYNC ALERT: $1"
    echo "$msg" >> "$ALERT_FILE"
}

# Mirrors ~/Data/sync exactly: `local current_branch` and the guard both live
# inside sync_repo(), and the guard is CALLED from that same scope. Bash uses
# dynamic scoping, so the local is visible to the nested function — but only
# while sync_repo is still on the stack. Defining the guard in one function and
# calling it from another loses the local entirely (and silently returns PUSH),
# which is what an earlier version of this test did to itself.
sync_repo_push() {
    local current_branch="$1"
    local name="testrepo"

    abort_if_stray_branch() {
        [ -z "$current_branch" ] && return 0
        [ "$current_branch" = "main" ] || [ "$current_branch" = "master" ] && return 0
        [ "${DATACORE_SYNC_ALLOW_BRANCH:-0}" = "1" ] && return 0
        sync_alert "ABORT push in $name — HEAD is on '$current_branch', not the default branch."
        return 1
    }

    abort_if_stray_branch || return 1
    return 0
}

check() {
    local branch="$1" expect="$2" override="${3:-0}"
    if DATACORE_SYNC_ALLOW_BRANCH="$override" sync_repo_push "$branch"; then
        got="PUSH"
    else
        got="ABORT"
    fi
    if [ "$got" = "$expect" ]; then
        echo "  PASS  branch=${branch:-<none>} override=$override -> $got"
    else
        echo "  FAIL  branch=${branch:-<none>} override=$override -> $got (expected $expect)"
        exit 1
    fi
}

echo "=== abort_if_stray_branch ==="
check "main"                PUSH
check "master"              PUSH
check "ops/b17-sprint-claim" ABORT
check "feature/whatever"     ABORT
check "ops/b17-sprint-claim" PUSH 1     # explicit override
check ""                     PUSH       # detached HEAD handled elsewhere

echo
echo "=== alert was raised for the stray-branch case? ==="
if grep -q "ABORT push" "$ALERT_FILE" 2>/dev/null; then
    echo "  PASS  alert written:"
    sed 's/^/        /' "$ALERT_FILE" | head -2
else
    echo "  FAIL  no alert written — silent abort is still silent"
    exit 1
fi
echo
echo "ALL PASS — a stray-branch push now aborts loudly instead of stranding work."
