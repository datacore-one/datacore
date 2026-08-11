#!/bin/sh
# Server-side membership + log-ownership check (DIP-0046 D5).
#
# POSIX sh, not Python, and that is the whole point of this file existing.
# The Python version (gitea-pre-receive.py) is the readable reference and is
# what the test suite exercises — but the Gitea container HAS NO PYTHON. It has
# sh, awk, sed, grep and git, and nothing else can be assumed. A hook that needs
# an interpreter the server lacks installs cleanly, sits there, and never runs:
# present, executable, correct, and completely inert. That is the same
# check-strength failure as a global core.hooksPath silently disabling per-repo
# hooks, reached from a different direction, and it is why the deploy procedure
# insists on proving a push that SHOULD be refused actually is.
#
# Two invariants:
#
#   MEMBERSHIP     the pusher appears in <space>/.datacore/members.yaml
#   LOG OWNERSHIP  a push only modifies .datacore/events/<actor>.jsonl for the
#                  actor doing the pushing
#
# Ownership is the load-bearing one: disjoint per-writer files are the ENTIRE
# reason a merge is a union that cannot conflict. One actor appending to
# another's log breaks that silently — the merge succeeds, the fold runs, and
# the events are attributed to someone who never emitted them.
#
# `genesis` is exempt: it is the import ROLE, written by whichever machine runs
# the ingest sweep, not a machine identity. It is therefore the one log the
# disjointness argument does not cover; if two machines ever run the sweep
# concurrently the importer needs a per-machine log, not a wider exemption here.
#
# Read from the INCOMING commit, never a worktree — a bare repo has none.
#
# REPORT-ONLY unless DATACORE_ENFORCE=1. A pre-receive rejection cannot be
# bypassed from the client and 0-personal is the operator's own daily notes, so
# a wrong rule here locks them out of their own space.
#
# stdin: "<old> <new> <ref>" per line.

# ACCOUNTS vs ACTORS — the limit of what a server can enforce here.
#
# Gitea reports the pushing ACCOUNT (GITEA_PUSHER_NAME), and every machine
# pushes to this Gitea as the same account. The server therefore CANNOT tell
# which actor wrote a log, and an ownership check against a machine name would
# flag every legitimate push. Measured on deployment: actor=gregor while
# members.yaml lists genesis/mac/nightshift/winston.
#
# So the split is: the server enforces what it can see — that the pushing
# account is admitted at all — and PER-ACTOR OWNERSHIP IS ENFORCED CLIENT-SIDE
# by .datacore/lib/hooks/log_ownership_guard.py, which runs on the machine that
# knows its own identity. Stated rather than papered over: on Gitea the
# single-writer invariant rests on the client hook plus config_drift watching
# that core.hooksPath stays set, not on this file.
ACCOUNTS="gregor"

ZERO="0000000000000000000000000000000000000000"
ACTOR="${DATACORE_ACTOR:-${GITEA_PUSHER_NAME:-${GL_USERNAME:-${USER:-unknown}}}}"
ENFORCE="${DATACORE_ENFORCE:-0}"
MEMBERS=".datacore/members.yaml"

violations=""
warnings=""

while read -r old new ref; do
    [ -z "$ref" ] && continue
    [ "$new" = "$ZERO" ] && continue          # deletion: no tree to inspect

    allowed=$(git cat-file -p "$new:$MEMBERS" 2>/dev/null \
              | sed -n '/^members:/,$p' | sed -n 's/^[[:space:]]*-[[:space:]]*\([^[:space:]]*\).*/\1/p')

    if [ "$old" = "$ZERO" ]; then
        files=$(git diff --name-only "$new" 2>/dev/null)
    else
        files=$(git diff --name-only "$old" "$new" 2>/dev/null)
    fi

    touched=$(printf '%s\n' "$files" \
              | sed -n 's#^.*\.datacore/events/\([A-Za-z0-9_-]*\)\.jsonl$#\1#p' \
              | sort -u)

    # No members.yaml -> the space predates D5. WARN, never reject: rejecting
    # every push to an unmigrated space is an outage, not a check.
    if [ -z "$allowed" ]; then
        [ -n "$touched" ] && warnings="${warnings}${ref}: no ${MEMBERS} in tree (unmigrated space)
"
        continue
    fi

    if [ -n "$touched" ] && ! printf '%s\n' "$allowed" | grep -qx "$ACTOR" \
       && ! printf '%s\n' "$ACCOUNTS" | tr ' ' '\n' | grep -qx "$ACTOR"; then
        violations="${violations}${ref}: pusher '${ACTOR}' not in ${MEMBERS} ($(printf '%s' "$allowed" | tr '\n' ' '))
"
    fi

    # Skip ownership when the pusher is an ACCOUNT: it is not attributable to a
    # machine, so any verdict here would be guesswork. See the note above.
    is_account=0
    for acc in $ACCOUNTS; do [ "$ACTOR" = "$acc" ] && is_account=1; done

    for a in $touched; do
        [ "$is_account" = "1" ] && continue
        [ "$a" = "$ACTOR" ] && continue
        [ "$a" = "genesis" ] && continue
        violations="${violations}${ref}: '${ACTOR}' modified ${a}.jsonl — logs are single-writer
"
    done
done

[ -n "$warnings" ] && printf 'datacore/warn: %s' "$warnings" >&2

if [ -n "$violations" ]; then
    if [ "$ENFORCE" = "1" ]; then
        printf 'datacore/REJECT: %s' "$violations" >&2
        printf '\nSee DIP-0046 §11. Append to your own log, or add yourself to %s in a separate reviewed commit.\n' "$MEMBERS" >&2
        exit 1
    fi
    printf 'datacore/would reject: %s' "$violations" >&2
fi
exit 0
