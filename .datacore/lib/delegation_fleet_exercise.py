#!/usr/bin/env python3
"""Part 2: real agents, real hosts, real ledger -- does delegation actually land?

`delegation_drill.py` proves the CONTROLS hold, offline and for nothing. It
cannot prove that four machines, four principals and two claim paths agree
about one shared log. This does, by putting real items in a real space and
letting each host's own dispatcher find them.

WHAT IT DOES NOT DO: seed another principal's item. Creating winston's
delegation from the mac would write into winston's log from a machine that is
not winston's, which is the DIP-0044 authorship violation this installation
has already paid for twice. So `seed` runs ON each host, AS that host's actor,
and the delegation reaches the others the only way it ever should -- by being
published and converged.

THE SHAPE IS NOT A RING, because the policy will not allow one.
`approvals_policy.yaml` lets winston delegate to miles, tris and data; miles to
tris and data; data and tris only to miles. Nothing may delegate TO winston: the
chief of staff hands work down and does not receive it. So:

    winston -> miles     winston -> data
    miles   -> data      data    -> miles

Every agent both gives and takes except winston, which by design only gives —
and `--assert-refusals` checks that the closing edge of the ring really is
refused rather than merely unused.

THE TASK IS SELF-CONTAINED AND THE CHECK IS SELF-VALIDATING. Counting the lines
of a file the space already has needs no network, no API and no judgement, and
the check recomputes the count in an isolated worktree of the agent's own
commit and compares. That cannot be satisfied by touching a file, and it cannot
be satisfied by writing the wrong number -- which is the difference between a
check that asserts an outcome and one that asserts that something appeared.

    delegation_fleet_exercise.py seed   --as winston --space ~/Data/8-firm
    delegation_fleet_exercise.py status --space ~/Data/8-firm
    delegation_fleet_exercise.py sweep  --space ~/Data/8-firm

`status` exits 0 only when every seeded item reached a terminal state.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

#: Who delegates to whom, and what each pair is for. Kept as data so `status`
#: and `sweep` read the same roster `seed` wrote from.
RING = {
    "winston": ["miles", "data"],
    "miles": ["data"],
    "data": ["miles"],
}

#: The closing edge the policy forbids. Asserted rather than assumed: a rule
#: nobody tests is a rule nobody knows is still there.
FORBIDDEN = ("data", "winston")

MARK = "delegation-fleet-exercise"
SOURCE = "org/next_actions.org"


def _task(to: str) -> tuple[str, str]:
    """(title, check) for one self-contained unit of work.

    The check recomputes the answer from the same committed tree the agent
    produced, so it asserts the OUTCOME. `test -s` would pass on a touched
    file; this does not.
    """
    out = f"drill/{to}-linecount.txt"
    title = f"count the lines of {SOURCE} and write only that number into {out}"
    check = (f'test -f {out} && test "$(wc -l < {SOURCE} | tr -d " ")" '
             f'= "$(tr -d "[:space:]" < {out})"')
    return title, check


def _item_id(frm: str, to: str, day: str) -> str:
    return f"{MARK}-{day}-{frm}-to-{to}"


def cmd_seed(args) -> int:
    from actor_identity import this_actor
    from ledger.log import EventLog
    from ledger.policy import guarded_append, PolicyError

    space = args.space.resolve()
    actor = args.as_ or this_actor()
    targets = RING.get(actor)
    if targets is None:
        print(f"{actor} delegates to nobody in this exercise; nothing to seed")
        return 0

    made = []
    for to in targets:
        title, check = _task(to)
        iid = _item_id(actor, to, args.day)
        payload = {"id": iid, "title": title, "assignee": to, "check": check,
                   "drill": MARK, "requested_by": actor}
        try:
            guarded_append(EventLog(space, actor), "item.create", payload)
            made.append(f"{actor} -> {to}")
        except PolicyError as exc:
            print(f"REFUSED {actor} -> {to}: {exc}")
            return 1

    if args.assert_refusals and actor == FORBIDDEN[0]:
        frm, to = FORBIDDEN
        title, check = _task(to)
        try:
            guarded_append(EventLog(space, frm), "item.create",
                           {"id": _item_id(frm, to, args.day) + "-forbidden",
                            "title": title, "assignee": to, "check": check,
                            "drill": MARK, "requested_by": frm})
        except PolicyError as exc:
            print(f"ok   {frm} may not delegate to {to}: {exc}")
        else:
            print(f"FAIL {frm} was allowed to delegate to {to}")
            return 1

    print(f"seeded {len(made)}: {', '.join(made)}")
    return 0


def _seeded(space: Path, day: str):
    from ledger.fold import fold
    from ledger.log import read_events
    state = fold(read_events(space))
    return {iid: it for iid, it in state.items.items()
            if iid.startswith(f"{MARK}-{day}-")}


def cmd_status(args) -> int:
    space = args.space.resolve()
    items = _seeded(space, args.day)
    if not items:
        print("nothing seeded for this day")
        return 1
    TERMINAL = {"completed", "verified", "dismissed"}
    rows = []
    for iid, it in sorted(items.items()):
        _, _, _, frm, _, to = iid.split("-", 5) if iid.count("-") >= 5 else ("", "", "", "?", "", "?")
        rows.append((iid, it.status, it.owner or "-"))
    width = max(len(r[0]) for r in rows)
    for iid, status, owner in rows:
        flag = "ok  " if status in TERMINAL else "... "
        print(f"  {flag} {iid:<{width}}  {status:<10} owner={owner}")
    done = sum(1 for _, s, _ in rows if s in TERMINAL)
    print(f"\n{done}/{len(rows)} reached a terminal state")
    if args.json:
        print(json.dumps([{"id": i, "status": s, "owner": o} for i, s, o in rows], indent=2))
    return 0 if done == len(rows) else 1


def cmd_sweep(args) -> int:
    """Close anything the exercise left open, and say so in the log.

    The events stay -- an append-only log does not forget a drill, and it
    should not. What sweep removes is the OPEN state, so a leftover item
    cannot be picked up by a scheduled dispatcher tomorrow.
    """
    from actor_identity import this_actor
    from ledger.log import EventLog
    space = args.space.resolve()
    actor = args.as_ or this_actor()
    closed = 0
    for iid, it in _seeded(space, args.day).items():
        if it.status in ("completed", "verified", "dismissed"):
            continue
        EventLog(space, actor).append(
            "item.dismiss", {"id": iid, "owner": actor, "kind": "dropped",
                             "reason": f"{MARK}: exercise over"})
        closed += 1
    print(f"closed {closed} open exercise item(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["seed", "status", "sweep"])
    ap.add_argument("--space", required=True, type=Path)
    ap.add_argument("--as", dest="as_", help="act as this principal (default: this host's actor)")
    ap.add_argument("--day", required=True, help="YYYY-MM-DD tag, so a rerun cannot collide")
    ap.add_argument("--assert-refusals", action="store_true",
                    help="also prove the policy still refuses the edge it forbids")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    return {"seed": cmd_seed, "status": cmd_status, "sweep": cmd_sweep}[a.command](a)


if __name__ == "__main__":
    raise SystemExit(main())
