#!/usr/bin/env python3
"""Sequencer CLI: emit and check finality seals (DIP-0042).

    ledger_seal.py emit   --space PATH     append a seal (sequencer only)
    ledger_seal.py status [--space PATH]   verify the latest seal
    ledger_seal.py status --all            every space with a ledger

WHO MAY SEAL. The sequencer is a designated role — Winston — not a consensus.
That is safe precisely because a seal is reproducible: `status` recomputes the
root from the named watermarks on any machine, so a wrong seal is detectable by
every reader rather than authoritative. `emit` refuses to run as a non-sequencer
by default so the role stays a decision rather than an accident of which box
happened to run a cron job.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))

from ledger.log import EventLog, read_events  # noqa: E402
from ledger.seal import build_seal_payload, latest_seal, verify_seal  # noqa: E402

SEQUENCER = os.environ.get("DATACORE_SEQUENCER", "winston")


def _actor() -> str:
    try:
        from actor_identity import this_actor
    except ImportError:
        import importlib.util as _ilu, pathlib as _pl
        _spec = _ilu.spec_from_file_location("actor_identity", _pl.Path(__file__).resolve().parent / "actor_identity.py")
        _m = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_m)
        this_actor = _m.this_actor
    return this_actor()


def _root() -> Path:
    return Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


def _spaces(root: Path) -> list[Path]:
    return [s for s in sorted(root.glob("[0-9]-*"))
            if (s / ".datacore" / "events").is_dir()]


def cmd_emit(space: Path, force: bool) -> int:
    actor = _actor()
    if actor != SEQUENCER and not force:
        print(f"refusing: this machine is '{actor}', the sequencer is "
              f"'{SEQUENCER}'. Finality is a designated role — run this on the "
              f"sequencer, or pass --force with a reason you can defend.")
        return 2

    events = read_events(space)
    if not events:
        print(f"  {space.name}: nothing to seal")
        return 0

    payload = build_seal_payload(events)
    prev = latest_seal(events)
    if prev and prev.state_root == payload["state_root"]:
        # Nothing has happened since the last seal. Emitting anyway would grow
        # the log with events that carry no information and make "when did
        # state last change?" unanswerable from the seal history.
        print(f"  {space.name}: unchanged since last seal ({prev.state_root[:12]})")
        return 0

    EventLog(space, actor).append("ledger.seal", payload)
    n = len(payload["watermarks"])
    print(f"  {space.name}: sealed {payload['state_root'][:12]} over {n} actor(s)")
    return 0


def cmd_status(space: Path) -> int:
    ok, detail = verify_seal(read_events(space))
    mark = {True: "ok  ", False: "FAIL", None: "n-a "}[ok]
    print(f"  {mark} {space.name:<14} {detail}")
    return 1 if ok is False else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ledger finality (DIP-0042)")
    ap.add_argument("op", choices=["emit", "status"])
    ap.add_argument("--space", type=Path)
    ap.add_argument("--root", type=Path, default=_root())
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="seal from a non-sequencer machine")
    a = ap.parse_args()

    targets = [a.space] if a.space else _spaces(a.root)
    if not targets:
        print(f"no space with a ledger under {a.root} — refusing to report success")
        return 2

    bad = 0
    for space in targets:
        if a.op == "emit":
            bad += 1 if cmd_emit(space, a.force) == 2 else 0
        else:
            bad += cmd_status(space)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
