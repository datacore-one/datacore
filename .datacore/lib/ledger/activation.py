"""Would activating the hub change this space? A read-only dry run (round 6).

The owner moves every space to the hub at once (2026-10-23). Activation must
not change what a space already says: the reversals already in effect keep
their effect (the owner's `hub.enable` freezes them, `hub.activation_reversals`),
and every later reversal follows the correction window. This check computes,
for one space as it stands, the state a reader sees today (the plain fold, the
old rule) and the state every reader would see right after activation
(admission against a cutover manifest of today's heads, the owner's frozen
reversals, the lease authority and the installation's policy), and reports
every difference. It writes nothing: no key, no config, no event.

    python3 -m ledger.activation <space> [<space> ...] [--json]

Exit 0: activation would change nothing in any space named. 1: it would (the
report says where). 2: a space could not be read.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DRY_GRANTOR = "activation-dry-run-grantor"
DRY_OWNER = "activation-dry-run-owner"


def _item_view(item) -> dict:
    p = item.payload or {}
    return {"status": item.status, "owner": item.owner, "assignee": p.get("assignee"), "title": p.get("title")}


def check(space_dir, *, policy=None, now_ms: int | None = None) -> dict:
    """{"space", "changes": bool, "refused_logs", "items", "spend", "orphans", "reversals"}."""
    import time
    from .events import compute_hash, event_body  # noqa: F401 -- loaded with the ledger
    from .fold import fold
    from .hub import activation_reversals
    from .hub_admission import Authority, Epoch, admit
    from .hub_policy import PolicyInputs
    from .log import read_events
    space = Path(space_dir)
    events = read_events(space)
    # What every reader sees now (the old rule, no hub yet). A deliberate plain
    # fold, allow-listed with its reason in the raw-fold guard (_RAW_FOLD_ALLOWED).
    today = fold(list(events))
    heads: dict = {}
    for e in events:
        chain = getattr(e, "log", None) or e.actor
        if type(e.seq) is int and (chain not in heads or e.seq > heads[chain][0]):
            heads[chain] = (e.seq, e.hash)
    reversals = activation_reversals(events)
    auth = Authority(space_id="activation-dry-run", hub_id="activation-dry-run",
                     grantors=frozenset({DRY_GRANTOR}), legacy=heads, owner=DRY_OWNER,
                     epochs=(Epoch(0, DRY_GRANTOR),), legacy_reversals=tuple(reversals))
    now = int(time.time() * 1000) if now_ms is None else now_ms
    admission = admit(list(events), auth, now_ms=now)
    inputs = policy if isinstance(policy, PolicyInputs) else PolicyInputs.load(policy)
    after = fold(admission.events, **auth.fold_inputs(inputs))
    items = {}
    for iid in sorted(set(today.items) | set(after.items)):
        a = _item_view(today.items[iid]) if iid in today.items else None
        b = _item_view(after.items[iid]) if iid in after.items else None
        if a != b:
            items[iid] = {"today": a, "after_activation": b,
                          "why": [h for h in (after.items[iid].history if iid in after.items else [])
                                  if "no-op" in h or "conflict" in h][-3:]}
    spend = {k: (today.spend.get(k), after.spend.get(k)) for k in sorted(set(today.spend) | set(after.spend))
             if today.spend.get(k) != after.spend.get(k)}
    orphans = sorted(set(after.orphans) - set(today.orphans))
    report = {"space": space.name, "events": len(events), "admitted": len(admission.events),
              "refused_logs": dict(sorted(admission.refused.items())), "items": items, "spend": spend,
              "new_orphans": orphans[:20], "reversals_kept": len(reversals),
              "same_root": today.state_root() == after.state_root()}
    report["changes"] = bool(report["refused_logs"] or items or spend or orphans)
    return report


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python3 -m ledger.activation", description=__doc__.split("\n")[0])
    ap.add_argument("spaces", nargs="+")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    worst, out = 0, []
    for s in a.spaces:
        try:
            r = check(s)
        except Exception as exc:  # noqa: BLE001 -- a dry run reports, it never raises
            r = {"space": Path(s).name, "error": f"{exc.__class__.__name__}: {exc}"}
            worst = max(worst, 2)
        else:
            worst = max(worst, 1 if r["changes"] else 0)
        out.append(r)
    if a.json:
        print(json.dumps(out, indent=2, sort_keys=True, default=str))
    else:
        for r in out:
            if "error" in r:
                print(f"{r['space']}: COULD NOT CHECK -- {r['error']}")
                continue
            print(f"{r['space']}: {'WOULD CHANGE' if r['changes'] else 'no change'} "
                  f"({r['events']} events, {r['admitted']} admitted, {r['reversals_kept']} reversal(s) kept)")
            for log, why in r["refused_logs"].items():
                print(f"  log {log} not admitted from {why}")
            for iid, d in list(r["items"].items())[:10]:
                print(f"  item {iid}: {d['today']} -> {d['after_activation']} {d['why']}")
            if len(r["items"]) > 10:
                print(f"  ... {len(r['items']) - 10} more items")
    return worst


if __name__ == "__main__":
    sys.exit(main())
