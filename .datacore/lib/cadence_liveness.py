#!/usr/bin/env python3
"""Write an artifact saying how many venture cadences are overdue.

WHY THIS IS ONLY A WRAPPER. `ventures/lib/cadence_engine.py` has computed
overdue cadences since it was written — `find_overdue_cadences()` does the
whole job. The detection was never missing. What was missing is the thing
this file adds: an artifact at a fixed path that a DIP-0035 job contract can
read, so that *nobody deciding to look* is no longer required.

That gap had a measured cost. On 2026-08-31, 49 cadences carried a recorded
last-run and 19 were stale past a week, 6 past ninety days. Among them:

    cto.daily.pr-review        136 days   (last run 2026-04-17)
    firm.weekly.portfolio-review 36 days

The first is the cadence whose entire job is catching unreviewed PRs — the
failure it was meant to catch went unnoticed because the catcher itself
stopped and nothing watched the catcher. The second is The Firm's own
portfolio review, whose log file carries a comment naming a next run that
never happened.

This is the closure mechanism in miniature (see
8-firm/1-tracks/ops/closure-diagnosis-2026-08-31.md): infrastructure must
leave an artifact and is checked; agent work asserts its own completion and
is not. A cadence is agent work, so it stopped silently for four months.

Usage:
    python3 cadence_liveness.py [--root DIR] [--grace-days N]
Artifact:
    ~/.datacore/state/cadence-liveness.log   (last line: "N cadence(s) overdue")
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

LIB = Path(__file__).resolve().parent
VENTURES_LIB = LIB.parent / "modules" / "ventures" / "lib"
if str(VENTURES_LIB) not in sys.path:
    sys.path.insert(0, str(VENTURES_LIB))

OUT = Path.home() / ".datacore" / "state" / "cadence-liveness.log"

#: Days past due before a cadence counts as overdue for ALERTING purposes.
#: cadence_engine flags a daily cadence the day after it runs, which is
#: correct for "what should I do today" and far too twitchy for a contract
#: that pages someone. The grace is what separates "due" from "broken".
DEFAULT_GRACE = 3


def collect(root: Path, grace: int, today: date | None = None) -> list:
    """Overdue cadences across every space, via the engine that owns this."""
    import yaml
    from cadence_engine import (cadence_log_path_for, find_overdue_cadences, own_cadences,
                            load_cadence_log_safe)

    today = today or date.today()
    rows = []
    for space in sorted(root.glob("[0-9]-*")):
        vy = space / "venture.yaml"
        if not vy.is_file():
            vy = space / ".datacore" / "venture.yaml"
        if not vy.is_file():
            continue
        try:
            data = yaml.safe_load(vy.read_text()) or {}
        except Exception:                       # noqa: BLE001
            continue
        # An archived venture is OFF (2026-09-04: forge, megaphone, fds,
        # datafund parked until PLUR runs well). Its cadence catalogue is not
        # a commitment; counting it made 34 "overdue" out of ventures nobody
        # had switched on. The runner and the heartbeat skip it; so does this.
        if str(data.get("stage", "")).lower() == "archived":
            continue
        roles = data.get("roles") or {}
        if not roles:
            continue
        # cadence_log_path_for FIRST. load_cadence_log_safe quarantines by
        # renaming the path it is handed, so passing the space directory
        # renames the space — which is exactly what happened on the first run
        # of this file and sent eight spaces to `<space>.broken-*.bak`.
        try:
            log = load_cadence_log_safe(cadence_log_path_for(space))
        except Exception:                       # noqa: BLE001
            continue
        try:
            # A cadence owned by an external agent (5-plur's cio is Tris on
            # hermes) runs where that agent runs and records nothing in this
            # fleet's shards, so this check can only ever call it overdue. The
            # heartbeat already excludes those roles from its own work
            # (own_cadences); the liveness must apply the same rule, or the
            # box's contract is red by construction (2026-09-05: three of the
            # last three "overdue" were Tris's).
            for c in own_cadences(find_overdue_cadences(roles, log, today=today), roles):
                if getattr(c, "days_overdue", 0) > grace:
                    rows.append((c.days_overdue, space.name, c.role,
                                 c.frequency, c.cadence_name))
        except Exception as exc:                # noqa: BLE001
            rows.append((-1, space.name, "?", "?", f"engine error: {exc}"))
    rows.sort(reverse=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("DATACORE_ROOT")
                    or str(Path.home() / "Data"))
    ap.add_argument("--grace-days", type=int, default=DEFAULT_GRACE)
    a = ap.parse_args()

    rows = collect(Path(a.root).expanduser(), a.grace_days)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"=== {date.today().isoformat()} cadence liveness "
             f"(grace {a.grace_days}d) ==="]
    for days, space, role, freq, name in rows:
        lines.append(f"  {days:5}d  {space:<12} {role}.{freq}.{name}")
    # LAST LINE IS THE CONTRACT. Anchored so a report listing overdue
    # cadences can never pass by containing a 0 somewhere in a name.
    lines.append(f"{len(rows)} cadence(s) overdue")
    OUT.write_text("\n".join(lines) + "\n")
    print(lines[-1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
