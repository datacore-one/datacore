#!/usr/bin/env python3
"""Step 2 of 5: put one real transaction through the whole loop, every day.

Every defect that mattered on 2026-09-18 was found by RUNNING the delegation
loop, not by the 3363 unit tests that passed all day beside them. The tests
encode what we already understood; this asserts the only thing a user cares
about, which is that real work finishes.

One item, end to end, on a schedule:

    create -> publish -> claim (by ANOTHER principal) -> execute -> verify
    -> complete

and an alert only if it does not reach `completed` inside the budget. Six of
that day's thirteen defects would have been caught on day one by this alone:
agents that never committed, a check whose input was gitignored, a structure
hook refusing the artifact, an agent runtime out of credits, a registry the
satellite could not resolve, and two hosts silently running stale code.

WHY A SEPARATE FILE FROM `delegation_fleet_exercise.py`. That one is an
operator's tool -- it seeds a ring by hand and a person reads the result. This
is a contract: one item, one verdict, a machine-readable artifact, safe to run
unattended for ever. Sharing the seeding code would drag the exercise's
multi-host ring into a job that must stay boring.

WHAT IT DELIBERATELY DOES NOT DO. It does not fail when the fleet is merely
busy or offline: a canary that pages for a closed laptop trains people to
ignore it. `--check` reads the previous run's outcome and is the half a job
contract asserts; `--run` does the work.

A FAILED LOCAL COMMIT IS `failed` (owner decision N5, 2026-09-23). This used to
write `blocked`, justified as "an unreachable remote is a condition". But the
only path that wrote it was the LOCAL `git commit` of the canary input -- nothing
here pushes -- and `blocked` passed the contract, so a space whose commits kept
failing reported a healthy loop every day for ever
(DatacoreSpec/NightshiftGates.lean, `Canary.blocked_forever`). And a `blocked`
verdict that is still on disk after BLOCKED_MAX_AGE_HOURS (48 h) fails
`--check`: a condition that lasts two days is a broken loop.

    delegation_canary.py --run    --space DIR [--assignee WHO]
    delegation_canary.py --check  [--max-age-hours N]

Exit 0 when the last canary completed inside its budget; 1 when it did not.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

#: Where the verdict lives. Under the runtime state directory, never inside a
#: space -- a canary that dirties the tree it verifies would fail the very
#: `_artifact_tree_clean` guard it is exercising.
STATE = Path(os.environ.get("DATACORE_STATE", str(Path.home() / ".datacore" / "state")))
RESULT = STATE / "delegation-canary.json"

MARK = "delegation-canary"
#: Generous on purpose. The point is "the loop is alive", not "the loop is
#: fast": a real agent turn took 37-41s on 2026-09-18, and a busy host or a
#: model retry must not page anyone.
DEFAULT_BUDGET_HOURS = 20

#: A `blocked` verdict older than this fails `--check` (decision N5). Nothing
#: writes `blocked` since N5; this ages out any that is still on disk.
BLOCKED_MAX_AGE_HOURS = 48


def _task(target: str, day: str) -> tuple[str, str, str]:
    """(title, check, artifact path) for one self-contained unit of work.

    The input is written and COMMITTED by `--run` before the item is created,
    because the check runs against an isolated worktree of the agent's commit
    and anything not in git is simply not there. That cost a live round to
    learn on 2026-09-18, when the task counted a generated, gitignored file.

    The artifact lives under `4-outbox`, which DIP-0015's structure hook
    permits; a top-level directory would be refused at commit time and no
    agent could ever satisfy the check.
    """
    src = f"4-outbox/{MARK}/input-{day}.txt"
    out = f"4-outbox/{MARK}/count-{day}.txt"
    title = f"count the lines of {src} and write only that number into {out}"
    check = (f'test -f {out} && test "$(wc -l < {src} | tr -d " ")" '
             f'= "$(tr -d "[:space:]" < {out})"')
    return title, check, src


def _write(verdict: str, **fields) -> None:
    RESULT.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    RESULT.write_text(json.dumps({"verdict": verdict, "at": time.time(),
                                  "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                  **fields}, indent=2) + "\n")
    print(f"canary: {verdict}" + (f" — {fields.get('detail','')}" if fields.get("detail") else ""))


def cmd_run(args) -> int:
    from actor_identity import this_actor
    from ledger.log import EventLog
    from ledger.policy import guarded_append, PolicyError
    import subprocess

    space = args.space.resolve()
    actor = this_actor()

    # RESOLVE THE PREVIOUS CANARY BEFORE SEEDING ANOTHER.
    #
    # This used to seed unconditionally, and RESULT holds one verdict. With
    # --run at 06:15 and --check at 08:45, every check found a canary two and a
    # half hours old and said "still in flight" -- so a canary nobody ever
    # completed was replaced by the next one before it could be judged, every
    # day, and the check could not fail on any path. It ran like that from
    # 2026-09-19, pointed at a space (8-firm) that no dispatcher for miles
    # sweeps, reporting a healthy delegation loop that had never once closed.
    #
    # Now the verdict does not depend on how the two jobs are ordered: an open
    # canary is judged here first. Finished -> recorded, and a new one is
    # seeded. Still inside its budget -> left alone, nothing new is seeded on
    # top of it. Past its budget -> recorded as FAILED and NOT replaced, so the
    # failure stays on disk for a full cycle of the contract that reads it; the
    # run after that starts fresh.
    try:
        prev = json.loads(RESULT.read_text())
    except (OSError, ValueError):
        prev = {}
    if prev.get("verdict") == "dispatched" and prev.get("item"):
        from ledger.fold import fold
        from ledger.log import read_events
        item = fold(read_events(space)).items.get(prev["item"])
        status = getattr(item, "status", None)
        age_h = (time.time() - float(prev.get("at", 0))) / 3600
        if status in ("completed", "verified"):
            _write("completed", item=prev["item"], owner=getattr(item, "owner", None),
                   detail=f"the loop closed in under {age_h:.1f}h")
        elif age_h <= args.max_age_hours:
            print(f"canary: previous still in flight ({status}) after {age_h:.1f}h of "
                  f"{args.max_age_hours}h; not seeding another on top of it")
            return 0
        else:
            _write("failed", item=prev["item"], detail=(
                f"still {status} after {age_h:.1f}h; delegation did not close end to end"))
            return 1

    day = time.strftime("%Y-%m-%dT%H%M", time.gmtime())
    iid = f"{MARK}-{day}"
    title, check, src = _task(args.assignee, day)

    path = space / src
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text("".join(f"line {n}\n" for n in range(1, 18)))
    for cmd in (["add", "--", src], ["commit", "-q", "-m", f"{MARK}: input {day}", "--", src]):
        r = subprocess.run(["git", "-C", str(space), *cmd], capture_output=True, text=True, timeout=60)
        if r.returncode and cmd[0] == "commit":
            # `failed`, not `blocked` (decision N5): this is a LOCAL commit, so
            # no remote is involved, and `blocked` passed the contract for ever.
            _write("failed", detail=f"could not commit the canary input: {r.stderr.strip()[-160:]}")
            return 1

    try:
        guarded_append(EventLog(space, actor), "item.create",
                       {"id": iid, "title": title, "assignee": args.assignee,
                        "check": check, "canary": True, "requested_by": actor})
    except PolicyError as exc:
        # The gate refusing is a real finding, not a transport condition.
        _write("failed", item=iid, detail=f"the gate refused the canary: {exc}")
        return 1

    _write("dispatched", item=iid, assignee=args.assignee,
           detail=f"awaiting {args.assignee}; verdict on the next --check")
    return 0


def cmd_check(args) -> int:
    """Did the last canary finish? This is the half a job contract asserts."""
    from ledger.fold import fold
    from ledger.log import read_events

    try:
        last = json.loads(RESULT.read_text())
    except (OSError, ValueError):
        _write("unknown", detail="no canary has run yet on this host")
        return 0                       # not a failure: nothing has been asked of it

    if last.get("verdict") == "blocked":
        age_h = (time.time() - float(last.get("at", 0))) / 3600
        if age_h > BLOCKED_MAX_AGE_HOURS:
            # A condition that outlasts two days is a broken loop (decision N5).
            _write("failed", detail=(
                f"blocked for {age_h:.1f}h (> {BLOCKED_MAX_AGE_HOURS}h): "
                f"{last.get('detail','')}"))
            return 1
        print(f"canary: blocked — {last.get('detail','')}")
        return 0                       # a short condition, not a broken loop

    iid = last.get("item")
    if not iid:
        print(f"canary: {last.get('verdict')} — {last.get('detail','')}")
        return 0 if last.get("verdict") != "failed" else 1

    space = args.space.resolve()
    item = fold(read_events(space)).items.get(iid)
    age_h = (time.time() - float(last.get("at", 0))) / 3600
    status = getattr(item, "status", None)

    if status in ("completed", "verified"):
        _write("completed", item=iid, owner=getattr(item, "owner", None),
               detail=f"the loop closed in under {age_h:.1f}h")
        return 0
    if age_h <= args.max_age_hours:
        print(f"canary: still in flight ({status}) after {age_h:.1f}h of {args.max_age_hours}h")
        return 0
    _write("failed", item=iid, detail=(
        f"still {status} after {age_h:.1f}h; delegation did not close end to end"))
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="store_true", help="seed one canary item")
    ap.add_argument("--check", action="store_true", help="report on the last one")
    ap.add_argument("--space", required=True, type=Path)
    ap.add_argument("--assignee", default="miles", help="the principal expected to do it")
    ap.add_argument("--max-age-hours", type=float, default=DEFAULT_BUDGET_HOURS)
    a = ap.parse_args(argv)
    if a.run == a.check:
        ap.error("exactly one of --run or --check")
    return cmd_run(a) if a.run else cmd_check(a)


if __name__ == "__main__":
    raise SystemExit(main())
