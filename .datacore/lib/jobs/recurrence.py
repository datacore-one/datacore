#!/usr/bin/env python3
"""recurrence.py — make a repeated job.verify failure read differently.

RESOLVES DIP-0035 OPEN QUESTION #2, which reads:

    "Remediation policy — job_verify.py currently only detects and alerts;
     whether a Phase 6+ follow-on should add auto-retry or auto-escalation on
     repeated failure is unresolved."

It does NOT invent a threshold. DIP-0031 already settled that for nightshift
task failures and this adopts it verbatim rather than picking a second number:

    "A task failing with the same category >=3 consecutive runs is a
     *recurring failure* — CoS triage escalates it as a decision item, and the
     executor MUST stop retrying it."

WHY IT WAS NEEDED. Measured on winston 2026-09-03: `box-projection-drift` had
22 consecutive failure records. The detector was correct every single time and
the drift it reported grew while it fired — 0-personal's projection went from
49 extra tasks to 339. Three other checks had been failing for days.

A 22nd consecutive failure rendered identically to a 1st, so the loudest
signal in the system was the one that had been normalised into background.
Every one of the five bug classes fixed this week was being reported before it
was found: box-briefing was red for six days, ledger-ingest aged 26 -> 50 ->
74 hours, and the nightshift watchdog said "This has happened 59 times in 48h"
in its own message.

So this is not another detector. It is the missing consumer of detections that
already work. The claim it adds is narrow and true: after three identical
runs, the interesting fact is no longer the failure — it is that nobody is
reading the alert.

NOT AUTO-REMEDIATION. DIP-0035 lists auto-retry alongside escalation; this
implements only escalation. Restarting a job whose producer is unscheduled
would loop forever, and a checker that repairs what it measures cannot be
trusted to measure it.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib

# DIP-0031: ">=3 consecutive runs is a recurring failure". Same number, on
# purpose -- two thresholds for one idea is bug class 1.
RECURRING_AFTER = 3

STATE = pathlib.Path(
    os.environ.get("DATACORE_STATE", pathlib.Path.home() / ".datacore" / "state")
) / "job-verify-recurrence.json"


def _load() -> dict:
    """Never raises. A corrupt or absent state file must not stop verification.

    Losing the count degrades escalation to a first-occurrence alert, which is
    exactly today's behaviour -- acceptable. Raising here would let a bookkeeping
    file take down the check that reports everything else.
    """
    try:
        d = json.loads(STATE.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, indent=1, sort_keys=True))
        tmp.replace(STATE)
    except OSError:
        pass


def record(job_name: str, failed: bool, *, today: str | None = None) -> dict:
    """Update and return this job's recurrence record.

    Returns {consecutive, first_failed, recurring}. A pass resets the count to
    zero and clears first_failed -- an intermittent job must not accumulate
    toward escalation across unrelated incidents.
    """
    today = today or datetime.date.today().isoformat()
    state = _load()
    rec = state.get(job_name) or {"consecutive": 0, "first_failed": None}

    if failed:
        rec["consecutive"] = int(rec.get("consecutive") or 0) + 1
        rec["first_failed"] = rec.get("first_failed") or today
        rec["last_failed"] = today
    else:
        rec = {"consecutive": 0, "first_failed": None, "last_passed": today}

    rec["recurring"] = rec["consecutive"] >= RECURRING_AFTER
    state[job_name] = rec
    _save(state)
    return rec


def describe(job_name: str, rec: dict, n_failures: int) -> str:
    """The alert line. Below the threshold it is unchanged from before.

    Above it, the sentence changes subject: the failure is no longer the news.
    Naming the count and the date is what makes a repeat visibly a repeat --
    the whole defect was that it did not.
    """
    n = int(rec.get("consecutive") or 0)
    if not rec.get("recurring"):
        return f"job.verify FAILED: {job_name} ({n_failures} failure(s))"
    since = rec.get("first_failed") or "unknown"
    return (
        f"job.verify RECURRING: {job_name} has failed {n} consecutive runs "
        f"since {since} ({n_failures} failure(s) this run). "
        f"Per DIP-0031 this is a recurring failure and needs a decision, not "
        f"another alert: fix the producer, or delete the check."
    )


def summary() -> list[dict]:
    """Every job currently in a recurring state, worst first."""
    out = [dict(job=k, **v) for k, v in _load().items() if v.get("recurring")]
    return sorted(out, key=lambda r: -int(r.get("consecutive") or 0))


def main() -> int:
    rows = summary()
    if not rows:
        print("no recurring job failures")
        return 0
    print(f"{len(rows)} job(s) in a recurring failure state "
          f"(>={RECURRING_AFTER} consecutive, DIP-0031):")
    for r in rows:
        print(f"  {r['consecutive']:>3}x since {r.get('first_failed')}  {r['job']}")
    print("\nEach of these needs a decision, not another alert.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
