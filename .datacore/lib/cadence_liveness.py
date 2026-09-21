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


def sunset_reviews(root: Path, today: date | None = None) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """Stage 7: every recurring commitment has a review date and sunsets by
    default. A venture may declare `cadence_reviews: {<cadence>: YYYY-MM-DD}`;
    this reports cadences past their review date (defend or drop) and cadences
    with no review date at all (the product's rule, unmet). Reported, not
    enforced: dropping a cadence is the owner's act."""
    import yaml
    today = today or date.today()
    past, undated = [], []
    for vy in sorted(root.glob("[0-9]-*/venture.yaml")):
        try:
            v = yaml.safe_load(vy.read_text()) or {}
        except Exception:  # noqa: BLE001
            continue
        if str(v.get("status") or "").lower() == "archived":
            continue
        reviews = v.get("cadence_reviews") or {}
        names = set()
        for role in (v.get("roles") or {}).values():
            for lst in ((role or {}).get("cadences") or {}).values():
                for c in (lst or []):
                    names.add(str(c))
        for c in sorted(names):
            due = reviews.get(c)
            if not due:
                undated.append((vy.parent.name, c))
                continue
            try:
                d = date.fromisoformat(str(due))
            except ValueError:
                undated.append((vy.parent.name, c)); continue
            if d <= today:
                past.append((vy.parent.name, c, d.isoformat()))
    return past, undated


def collect(root: Path, grace: int, today: date | None = None) -> list:
    """Overdue cadences across every space, via the engine that owns this."""
    import yaml
    from cadence_engine import (FREQUENCY_WINDOWS, cadence_log_path_for, cadence_observation,
                            find_overdue_cadences, own_cadences, load_cadence_log_safe)

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
                # PAST DUE, which is what DEFAULT_GRACE is documented to measure.
                # The engine's days_overdue is days since the LAST RUN -- right
                # for ordering today's work, wrong against a grace: a weekly
                # cadence reads 7 on the very day it falls due, so this alerted
                # before the cadence had any chance to run. On 2026-09-17 two
                # weekly cadences last run 09-10 were reported "7d overdue" at
                # 07:40Z on their due date, having read 0 the day before.
                # A cadence that has NEVER run keeps the old measure: the
                # engine reports it at exactly one window, and never having
                # run is the broken case this check exists to catch.
                window = FREQUENCY_WINDOWS.get(c.frequency)
                ran = cadence_observation(roles, log, c.role, c.frequency, c.cadence_name) is not None
                past_due = (c.days_overdue - window.days) if (ran and window) else c.days_overdue
                if past_due > grace:
                    rows.append((past_due, space.name, c.role,
                                 c.frequency, c.cadence_name))
        except Exception as exc:                # noqa: BLE001
            rows.append((-1, space.name, "?", "?", f"engine error: {exc}"))
    rows.sort(reverse=True)
    return rows


def _unrunnable(root: Path, spaces: set[str]) -> dict[str, str]:
    """For each space with overdue cadences, WHY nothing is running them.

    Eight cadences in one space going overdue on one day is one cause, and the
    count never said which. 6-meridian's sat overdue from 2026-09-15 and
    alerted daily for a week. The first explanation written here was wrong --
    it said the executing host did not carry the space, from an `ls` that a
    `head -12` had truncated. The space was there all along.

    The heartbeat had been stating the real reason every thirty minutes, in a
    journal nobody reads: "venture skipped: 6-meridian: Invalid venture
    configuration: roles". On 2026-09-15 the loader began rejecting unsupported
    cadence frequencies (ventures 51892ee), and that venture had declared
    `every_15min` and `every_4h` since April. The rejection is per VENTURE, so
    two unsupported keys stopped eight valid daily cadences as well.

    So this asks the loader itself, and reports field paths -- never values,
    which is the loader's own rule for diagnostics.
    """
    out: dict[str, str] = {}
    try:
        lib = Path(__file__).resolve().parent.parent / "modules" / "ventures" / "lib"
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        import yaml
        from pydantic import ValidationError
        from venture_loader import VentureConfig
    except Exception:  # noqa: BLE001 -- no loader here means no diagnosis, not a crash
        return out
    for name in spaces:
        cfg = root / name / "venture.yaml"
        if not cfg.exists():
            continue
        try:
            VentureConfig.model_validate(yaml.safe_load(cfg.read_text()) or {})
        except ValidationError as exc:
            where = sorted({".".join(str(x) for x in e["loc"]) + ": " + e["msg"].replace("Value error, ", "")
                            for e in exc.errors(include_input=False, include_url=False)})
            out[name] = ("venture.yaml is REJECTED by the loader, so the heartbeat skips the "
                         "whole venture and none of its cadences can run — " + "; ".join(where[:4]))
        except Exception as exc:  # noqa: BLE001
            out[name] = f"venture.yaml could not be read ({type(exc).__name__})"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("DATACORE_ROOT")
                    or str(Path.home() / "Data"))
    ap.add_argument("--grace-days", type=int, default=DEFAULT_GRACE)
    a = ap.parse_args()

    root = Path(a.root).expanduser()
    today = date.today()
    rows = collect(root, a.grace_days)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"=== {today.isoformat()} cadence liveness "
             f"(grace {a.grace_days}d) ==="]
    for days, space, role, freq, name in rows:
        lines.append(f"  {days:5}d  {space:<12} {role}.{freq}.{name}")
    # WHY, PER SPACE, ONCE. Eight cadences in one space going overdue on the
    # same day is one cause, not eight, and the count alone never said which.
    # 6-meridian's eight sat overdue from 2026-09-15 and alerted daily: the
    # space is simply not cloned on the host that ticks cadences, so nothing
    # could ever have run them and no amount of waiting would change it. An
    # alert that cannot be acted on gets read as noise, and then the next one
    # does too.
    for space_name, why in sorted(_unrunnable(root, {r[1] for r in rows}).items()):
        lines.append(f"  note: {space_name} — {why}")
    # LAST LINE IS THE CONTRACT. Anchored so a report listing overdue
    # cadences can never pass by containing a 0 somewhere in a name.
    _past, _undated = sunset_reviews(root, today)
    for _sp, _c, _d in _past:
        lines.append(f"  past review: {_sp} {_c} (review due {_d})")
    lines.append(f"{len(rows)} cadence(s) overdue; sunset review: {len(_past)} past due, {len(_undated)} without a review date")
    OUT.write_text("\n".join(lines) + "\n")
    print(lines[-1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
