# Recovery runbook

One section per known recovery situation (audit C15, OI-21). Each gives what
you see, what it means, and the procedure as commands. Follow them in order;
every step is safe to run again. `<space>` is the space named in the error
(for example `0-personal`), `<writer>` the log's file stem (`mac`,
`nightshift`, ...).

After any procedure, the health check is the same:

```bash
python3 ~/Data/.datacore/lib/ledger_cli.py verify --space ~/Data/<space>
python3 ~/Data/.datacore/lib/v2_verify.py --quick
```

History is never rewritten (owner decision 6, 2026-09-26). No procedure here
edits or deletes an event; the only way to cancel one is an in-ledger void.

## Stale log (StaleLogError: "The log was rewound")

**You see:** an append refused with `StaleLogError: <writer>.jsonl ends at seq N
but this machine already wrote seq M`.

**It means:** this machine's sequence mark (`.datacore/state/seq-hwm/<writer>.seq`,
outside git) remembers writing further than the log file now goes. Either the
file was rewound (a bad checkout or merge dropped events that exist somewhere
else), or the mark itself is wrong (restored from another machine's backup,
corrupted). Appending anyway would reuse a seq and fork the log.

**Procedure.** First get every copy of the log that exists, then clear the mark
only when no copy anywhere runs further. Clearing it while the events still sit
on a park branch makes a recoverable situation permanent (2026-09-16: eleven
events thought lost were on a park branch the whole time).

```bash
SPACE=~/Data/<space>
LIB=~/Data/.datacore/lib
  # 1. Converge: the missing events may simply not be pulled yet.
if git -C "$SPACE" rev-parse --git-dir >/dev/null 2>&1 && git -C "$SPACE" remote get-url origin >/dev/null 2>&1; then
  git -C "$SPACE" pull -q --no-rebase --ff-only || echo "pull did not fast-forward: see 'Forked log' below"
fi
  # 2. Search EVERY history (branches, park refs, unreferenced commits) for a longer copy.
found=0
for mark in "$SPACE"/.datacore/state/seq-hwm/*.seq; do
  [ -e "$mark" ] || continue
  writer=$(basename "$mark" .seq)
  if git -C "$SPACE" rev-parse --git-dir >/dev/null 2>&1; then
    out=$(python3 "$LIB/ledger_restore_prefix.py" --space "$SPACE" --actor "$writer" --find)
    echo "$writer: $out"
    case "$out" in "no commit in any history"*) ;; *) found=1 ;; esac
  fi
done
  # 3. A longer copy exists: restore it (append-only, proven prefix + verifying chain),
  #    then stop here. Otherwise the mark is what is wrong: clear the marks that run
  #    ahead of their log, and only those.
if [ "$found" = 1 ]; then
  echo "restore with: python3 $LIB/ledger_restore_prefix.py --space $SPACE --actor <writer> --from <commit> --apply"
else
  python3 - "$SPACE" <<'PY'
import json, sys
from pathlib import Path
space = Path(sys.argv[1])
for mark in sorted((space / ".datacore" / "state" / "seq-hwm").glob("*.seq")):
    log = space / ".datacore" / "events" / f"{mark.stem}.jsonl"
    lines = [l for l in log.read_text().splitlines() if l.strip()] if log.exists() else []
    tail = json.loads(lines[-1])["seq"] if lines else -1
    if int(mark.read_text().strip() or -1) > tail:
        mark.unlink()
        print(f"cleared {mark.name}: the log ends at seq {tail} and no copy anywhere runs further")
PY
fi
```

Then run the health check at the top of this page. The next append continues
the chain from the log's real tail.

## Edit conflict (unresolved replicated edits)

**You see:** ingest or `project` stops with "unresolved replicated edits;
preserve the file and reconcile event conflicts", and the hourly cycle for that
space stops with it.

**It means:** a conditional edit was refused because the item moved underneath
it (a concurrent edit from another writer). The ledger keeps the refused intent
on the item as `edit_conflicts` instead of dropping it, and the space waits
until someone decides.

**Procedure.** Look first (dry run), then clear by the ledger's own route. The
clearing event rewrites the item's own recorded state over itself, pinned field
by field, so it cannot land on an item that moved again.

```bash
python3 ~/Data/.datacore/lib/ledger_resolve_conflict.py --space ~/Data/<space> --all
python3 ~/Data/.datacore/lib/ledger_resolve_conflict.py --space ~/Data/<space> --all --apply
```

If the refused intent was the edit you wanted, make it again now that the item
is current (`org_workspace_adapter.py update ...`).

## Forked log (ledger fork: same seq, different events)

**You see:** `v2_verify` row "no forked logs" FAILs with `FORKED: <space>`, or a
push/merge is refused by the relay with "(actor, seq) differ from origin — a fork".

**It means:** two copies of one writer's log disagree about what one seq is.
Each copy verifies on its own; only a comparison with origin shows it. Origin is
the published history every machine converges on, so origin's version stands.

**Procedure.** Name the collisions, keep this machine's copy as evidence, take
origin's log, and re-enter any intent that existed only here as NEW events.

```bash
SPACE=~/Data/<space>
git -C "$SPACE" fetch -q origin
python3 -c "import sys; sys.path.insert(0, '$HOME/Data/.datacore/lib'); from ledger.fork import detect; from pathlib import Path; print(detect(Path('$SPACE')))"
  # keep the local copy (nothing is deleted), then take origin's log for the forked writer
git -C "$SPACE" update-ref "refs/fork-evidence/$(date +%Y%m%d)" HEAD
git -C "$SPACE" checkout origin/main -- .datacore/events/<writer>.jsonl
python3 ~/Data/.datacore/lib/ledger_cli.py verify --space "$SPACE"
```

The local-only events are still readable at `refs/fork-evidence/<date>`. Re-create
what they did through the normal tools; do not copy their lines back. Then
follow "Stale log" above if the next append on this machine is refused.

## Stranded run branch

**You see:** `git_fleet_sync` or `v2_verify` "no stranded commits" names a
`nightshift/run-*` (or `<writer>-run-<date>`) branch whose commits exist on no
remote, older than a day.

**It means:** a run did its work on a branch that never landed. Its deliverables
and its branch-scoped log (`<writer>-run-<date>.jsonl`, a separate file) are at
risk of being lost with the machine.

**Procedure.** List what is stranded, recover the files the branches added (a
dry run first; it never replays edits to files the default branch has moved on
from, and never deletions), then push.

```bash
git -C ~/Data/<space> branch --no-merged origin/main --list 'nightshift/run-*'
python3 ~/Data/.datacore/lib/nightshift_recover_stranded.py ~/Data
python3 ~/Data/.datacore/lib/nightshift_recover_stranded.py ~/Data --execute
python3 ~/Data/.datacore/lib/ledger_transport.py status
```

## Bad event on origin (voiding an event)

**You see:** a published event that is wrong: an item created by mistake, a
completion that did not happen, a record under the wrong writer.

**It means:** the event is part of every machine's history. It is never edited
or deleted; it is cancelled by an authorised void record, appended to the
ledger, and no one voids their own events (owner decision 6).

**Procedure.** Run by an authorised voider who is NOT the event's writer.

```bash
grep -n '"seq": <n>,' ~/Data/<space>/.datacore/events/<writer>.jsonl | head -1
python3 ~/Data/.datacore/lib/ledger_cli.py void --space ~/Data/<space> --log <writer>.jsonl --seq <n> --reason '<why it is wrong>'
python3 ~/Data/.datacore/lib/ledger_cli.py verify --space ~/Data/<space>
python3 ~/Data/.datacore/lib/ledger_transport.py status
```

If `void` refuses, the refusal names why (not authorised, your own event, the
event changed); that is the answer, not an obstacle to work around.

## Lost signing key (key rotation)

**You see:** `~/.datacore/keys/<writer>.key` is gone (a rebuilt machine, a wiped
home), or you believe it was exposed.

**It means, today:** signing is not required (owner decision 1, 2026-09-26: the
write-side gate closes the hand-written class without keys; signing returns with
FDS-ID identity). A lost key stops nothing: appends continue, and verify without
`--strict` does not ask for signatures. A new key is generated on the writer's
next signed append.

**Procedure.** Confirm history still verifies, then collect the new public key so
readers can check future signatures. `ledger_keys_collect.py` accepts a key only
when it verifies that writer's signed events, so a replaced key is reported, not
written; that report is the record of the rotation until FDS-ID lands.

```bash
python3 ~/Data/.datacore/lib/ledger_cli.py verify --space ~/Data/<space>
python3 ~/Data/.datacore/lib/ledger_keys_collect.py --hosts <host>
```

Do not run `verify --strict` as a health check while signing is off.

## Phase-1 rollback (deactivating Phase 1 for a space)

**You see:** a space flipped to Phase 1 (the ledger authors
`org/next_actions.org`) misbehaves: generated org is wrong, writes are refused,
the projection will not settle.

**It means:** the space should go back to Phase 0, where the authored org file is
the durable source and the ledger mirrors it.

**Procedure.** Dry run, then apply. Reverse removes `.datacore/ledger-phase` and
the ignore line and commits the current generated file as the authored file
again, so nothing on the task list is lost.

```bash
python3 ~/Data/.datacore/lib/ledger_phase1_flip.py --space <space> --reverse
python3 ~/Data/.datacore/lib/ledger_phase1_flip.py --space <space> --reverse --apply
python3 ~/Data/.datacore/lib/ledger_cli.py verify --space ~/Data/<space>
```
