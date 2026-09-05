#!/usr/bin/env python3
"""Reliability scoreboard: did the box do what it was scheduled to do today,
and did we hear about it when it did not.

Six conditions, one line per day, a streak, a level. The definition lives in
2-datacore/1-tracks/ops/reliability-slo.md; this file computes it from what
the box already writes. Nothing here is a judgment call: every condition is
a file and a rule, so two people running it get the same line.

  R1 delivered   no job in a recurring state (job-verify-recurrence.json)
  R2 loud        every failed unit today has a sent alert; no burst-suppressed unit alert
  R3 unattended  interventions.log has no line for today
  R4 data-safe   backup <=26h offsite=ok; restore check <=8d ok; fleet sync last result ok
  R5 reachable   >=99.5% UP samples today in the uptime probe log (when present)
  R6 rebuildable the daily `cos-server-setup.sh --verify` log has 0 FAIL lines today

Level: 3 while the streak is under 7 days, 4 from 7, 5 from 30 (the N=2
stranger-install gate is recorded by hand in the SLO page, not here).

Usage: reliability_scoreboard.py [--date YYYY-MM-DD] [--state DIR] [--json]
Appends one line to <state>/reliability-scoreboard.log and prints it.
Exit 0 when every condition passed today, 1 otherwise (so a job contract
can watch the artifact, and a failing day is itself loud).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path

HOME = Path(os.environ.get("COS_HOME", str(Path.home())))
STATE = Path(os.environ.get("DATACORE_STATE", HOME / ".datacore" / "state"))
COS = Path(os.environ.get("COS_DIR", HOME / ".datacore" / "cos"))


def _lines(p: Path) -> list[str]:
    try:
        return p.read_text(errors="replace").splitlines()
    except OSError:
        return []


def _age_hours(p: Path, now: float) -> float | None:
    try:
        return (now - p.stat().st_mtime) / 3600
    except OSError:
        return None


def r1_delivered(state: Path) -> tuple[bool, str]:
    rec = state / "job-verify-recurrence.json"
    try:
        d = json.loads(rec.read_text())
    except (OSError, ValueError):
        return True, "no recurrence state (no job has failed three times)"
    recurring = sorted(k for k, v in d.items() if isinstance(v, dict) and v.get("recurring"))
    return (not recurring), ("none recurring" if not recurring else "recurring: " + ", ".join(recurring))


def r2_loud(cos: Path, day: str) -> tuple[bool, str]:
    sup = [l for l in _lines(cos / "alerts" / "suppressed.log") if l.startswith(f"[{day}") and "burst" in l and "unit-failed:" in l]
    failed = _failed_units()
    sent = _lines(cos / "alerts" / ".sent-log")
    day_start = dt.datetime.fromisoformat(day).timestamp()
    sent_units = {l.split("unit-failed:", 1)[1].strip() for l in sent
                  if "unit-failed:" in l and l.split(" ", 1)[0].isdigit() and float(l.split(" ", 1)[0]) >= day_start}
    silent = sorted(u for u in failed if u not in sent_units)
    ok = not sup and not silent
    notes = []
    if sup:
        notes.append(f"{len(sup)} unit alert(s) burst-suppressed")
    if silent:
        notes.append("failed without a sent alert: " + ", ".join(silent))
    return ok, ("; ".join(notes) or f"{len(sent_units)} unit alert(s) sent, none suppressed")


def _failed_units() -> set[str]:
    try:
        out = subprocess.run(["systemctl", "list-units", "--state=failed", "--no-pager", "--plain", "--no-legend"],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {l.split()[0] for l in out.splitlines() if l.strip().endswith(".service") or ".service" in l.split()[0:1]}


def r3_unattended(cos: Path, day: str) -> tuple[bool, str]:
    n = sum(1 for l in _lines(cos / "interventions.log") if l.startswith(day))
    return n == 0, ("no hand intervention" if n == 0 else f"{n} hand intervention(s) logged")


def r4_data_safe(cos: Path, state: Path, now: float) -> tuple[bool, str]:
    notes, ok = [], True
    bl = [l for l in _lines(cos / "backup.log") if "backup ok" in l]
    age = _age_hours(cos / "backup.log", now)
    if not bl or age is None or age > 26 or "offsite=ok" not in bl[-1]:
        ok = False; notes.append("backup: " + (bl[-1].split("backup ok: ", 1)[-1][:60] if bl else "no 'backup ok' line") + (f" ({age:.0f}h)" if age is not None else ""))
    rl = [l for l in _lines(cos / "restore-check.log") if "restore-check" in l]
    rage = _age_hours(cos / "restore-check.log", now)
    if not rl or rage is None or rage > 8 * 24 or "restore-check ok" not in rl[-1]:
        ok = False; notes.append("restore check: " + (rl[-1][-60:] if rl else "never run"))
    fs = _lines(state / "fleet-sync.log")
    if fs and any(l.startswith("FAIL") for l in fs[-3:]):
        ok = False; notes.append("fleet sync: " + next(l for l in reversed(fs) if l.startswith("FAIL"))[:70])
    return ok, ("; ".join(notes) or "backup offsite, restore proven, fleet converging")


def r5_reachable(state: Path, day: str) -> tuple[bool, str]:
    rows = [l for l in _lines(state / "cos-uptime.log") if l.startswith(day)]
    if not rows:
        return True, "no probe log on this host (measured on the prober)"
    up = sum(1 for l in rows if " UP " in l)
    ratio = up / len(rows)
    return ratio >= 0.995, f"{up}/{len(rows)} probes UP ({ratio:.1%})"


def r6_rebuildable(cos: Path, day: str) -> tuple[bool, str]:
    rows = _lines(cos / "verify-daily.log")
    today = [l for l in rows if l.startswith(day)]
    if not today:
        return False, "no --verify run logged today"
    fails = [l for l in today if " FAIL " in l or l.split(" ", 1)[-1].startswith("FAIL")]
    return not fails, (f"{len(fails)} FAIL line(s)" if fails else "0 FAIL")


def level(streak: int) -> int:
    return 5 if streak >= 30 else 4 if streak >= 7 else 3


def compute(day: str, state: Path, cos: Path, now: float | None = None) -> dict:
    now = now or dt.datetime.now().timestamp()
    checks = {
        "R1": r1_delivered(state), "R2": r2_loud(cos, day), "R3": r3_unattended(cos, day),
        "R4": r4_data_safe(cos, state, now), "R5": r5_reachable(state, day), "R6": r6_rebuildable(cos, day),
    }
    passed = all(ok for ok, _ in checks.values())
    log = state / "reliability-scoreboard.log"
    prev = [l for l in _lines(log) if re.match(r"^\d{4}-\d{2}-\d{2} ", l) and not l.startswith(day)]
    streak = 0
    if prev:
        m = re.search(r" streak=(\d+) ", prev[-1])
        last_day = prev[-1].split(" ", 1)[0]
        yesterday = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
        if m and last_day == yesterday and " PASS " in prev[-1]:
            streak = int(m.group(1))
    streak = streak + 1 if passed else 0
    return {"day": day, "pass": passed, "streak": streak, "level": level(streak),
            "checks": {k: {"ok": ok, "note": note} for k, (ok, note) in checks.items()}}


def line(r: dict) -> str:
    flags = " ".join(f"{k}={'ok' if v['ok'] else 'FAIL'}" for k, v in r["checks"].items())
    notes = "; ".join(f"{k}: {v['note']}" for k, v in r["checks"].items() if not v["ok"])
    return f"{r['day']} {'PASS' if r['pass'] else 'FAIL'} streak={r['streak']} level={r['level']} {flags}" + (f" | {notes}" if notes else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--state", default=str(STATE))
    ap.add_argument("--cos", default=str(COS))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    state, cos = Path(a.state), Path(a.cos)
    r = compute(a.date, state, cos)
    out = line(r)
    if not a.no_write:
        state.mkdir(parents=True, exist_ok=True)
        log = state / "reliability-scoreboard.log"
        kept = [l for l in _lines(log) if not l.startswith(a.date + " ")]
        log.write_text("\n".join(kept + [out]) + "\n")
    print(json.dumps(r, indent=2) if a.json else out)
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
