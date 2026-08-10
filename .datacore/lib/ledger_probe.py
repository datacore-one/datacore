#!/usr/bin/env python3
"""End-to-end ledger probe — proves one machine can drive the whole DIP-0034/0043 path.

The roster (.datacore/registry/infrastructure.yaml) warns that a partial ledger
rollout is worse than none: a machine on an older write path either has its
writes refused by the projector guard or overwrites generated state. Checking
that the *files* are present does not prove that, and neither does importing the
package — the failure mode is a machine that writes an event nobody can fold.

So this writes a real item through a real EventLog into a THROWAWAY space,
folds it, verifies the hash chain, and projects it back to org. If the org text
that comes out does not contain the item we put in, this machine cannot
participate in the ledger, whatever its `git rev-parse` says.

Throwaway by construction: everything happens under a TemporaryDirectory, so a
probe can never append to a real space's log. Exit 0 = this machine is good.

    python3 .datacore/lib/ledger_probe.py [--actor NAME]
"""
from __future__ import annotations

import argparse
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger.fold import fold
from ledger.log import EventLog, read_events
from ledger.projector import project
from ledger.verify import verify_chain

PROBE_ID = "probe-0000-0000-0000-ledgerprobe"
PROBE_TITLE = "ledger probe — end to end"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--actor", default=socket.gethostname())
    args = ap.parse_args()
    actor = args.actor

    steps: list[tuple[str, bool, str]] = []

    def step(name: str, ok: bool, detail: str = "") -> bool:
        steps.append((name, ok, detail))
        return ok

    with tempfile.TemporaryDirectory(prefix="ledger-probe-") as tmp:
        space = Path(tmp) / "probe-space"
        (space / ".datacore" / "events").mkdir(parents=True)

        # 1. write — the path that a stale machine fails at, because events.py
        #    validates the type against EVENT_TYPES.
        try:
            log = EventLog(space, actor)
            log.append("item.create", {
                "id": PROBE_ID,
                "title": PROBE_TITLE,
                "state": "TODO",
                # project() filters on payload["space"]; an item without it is
                # silently dropped from every space-scoped projection.
                "space": "probe",
                "org": {"tags": ["probe"]},
            })
            log.append("item.claim", {"id": PROBE_ID, "owner": actor})
            wrote = True
            detail = ""
        except Exception as exc:  # noqa: BLE001 — the probe reports, never raises
            wrote, detail = False, f"{type(exc).__name__}: {exc}"
        if not step("write", wrote, detail):
            return report(actor, steps)

        # 2. a NEW event type — the specific thing a pre-0e2aad5 machine rejects.
        try:
            log.append("item.grant", {"id": PROBE_ID, "owner": actor})
            newtype, detail = True, ""
        except Exception as exc:  # noqa: BLE001
            newtype, detail = False, f"{type(exc).__name__}: {exc}"
        step("new event types", newtype, detail)

        # 3. chain integrity over what we just wrote.
        path = space / ".datacore" / "events" / f"{actor}.jsonl"
        try:
            problems = verify_chain(path)
            step("chain verifies", not problems, "; ".join(problems[:2]))
        except Exception as exc:  # noqa: BLE001
            step("chain verifies", False, f"{type(exc).__name__}: {exc}")

        # 4. fold — does the item come back with the state we drove it to?
        try:
            state = fold(read_events(space))
            item = state.items.get(PROBE_ID)
            step("fold", item is not None,
                 "item absent from fold" if item is None else f"owner={item.owner}")
        except Exception as exc:  # noqa: BLE001
            state, item = None, None
            step("fold", False, f"{type(exc).__name__}: {exc}")

        # 5. projection — the org text must actually contain the item. This is
        #    the assertion that a "the import worked" check cannot make.
        if state is not None:
            try:
                text = project(state, space="probe").text
                ok = PROBE_TITLE in text
                step("projects to org", ok,
                     "title missing from projected org" if not ok else f"{len(text)} chars")
            except Exception as exc:  # noqa: BLE001
                step("projects to org", False, f"{type(exc).__name__}: {exc}")

    return report(actor, steps)


def report(actor: str, steps: list[tuple[str, bool, str]]) -> int:
    failed = [s for s in steps if not s[1]]
    for name, ok, detail in steps:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
    if failed:
        print(f"ledger-probe {actor}: {len(failed)} FAILED")
        return 1
    print(f"ledger-probe {actor}: all {len(steps)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
