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
import time
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
    """Every unit that failed TODAY has a sent alert, and no unit alert was
    burst-suppressed. "Failed today" comes from the journal, not from
    `systemctl --state=failed` at scoreboard time: a unit that failed at 03:00
    and was green again by 08:10 is exactly the failure this must not miss.
    When the journal is unreadable the currently failed units are the
    fallback and the note says so."""
    sup = [l for l in _lines(cos / "alerts" / "suppressed.log") if l.startswith(f"[{day}") and "burst" in l and "unit-failed:" in l]
    failed, source = _failed_units_today(day)
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
    return ok, ("; ".join(notes) or f"{len(failed)} unit failure(s) today, {len(sent_units)} alerted, none suppressed ({source})")


def _journal(args: list[str]) -> str:
    """The system journal. Plain first (the installer puts the CoS user in
    systemd-journal); `sudo -n` as the fallback on a box where that has not
    happened yet, so the day is measured rather than reported unreadable."""
    for cmd in (["journalctl"], ["sudo", "-n", "journalctl"]):
        try:
            out = subprocess.run([*cmd, "--no-pager", "-q", *args], capture_output=True, text=True, timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        if out.strip():
            return out
    return ""


_FAILED_RE = re.compile(r"systemd\[1\]: ([A-Za-z0-9@._-]+\.service): Failed with result")


def _failed_units_today(day: str) -> tuple[set[str], str]:
    out = _journal(["--since", f"{day} 00:00"])
    if out:
        return {m.group(1) for m in _FAILED_RE.finditer(out) if not m.group(1).startswith("alert@")}, "journal"
    return _failed_units(), "journal unreadable; currently failed units only"


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
    fs_ok, fs_note = _fleet_sync_state(state, now)
    if not fs_ok:
        ok = False; notes.append("fleet sync: " + fs_note)
    return ok, ("; ".join(notes) or "backup offsite, restore proven, fleet converging")


FLEET_SYNC_UNIT = "datacore-fleet-sync.service"
FLEET_SYNC_MAX_AGE_H = 14  # the timer fires at 06:10 and 18:10; a run older than this means the timer stopped


def _unit_show(unit: str) -> dict:
    """systemd's own record of a unit's last run, or {} where there is no such unit."""
    try:
        out = subprocess.run(["systemctl", "show", "-p", "LoadState", "-p", "Result", "-p", "ExecMainExitTimestamp", unit],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    d = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    return d if d.get("LoadState") == "loaded" else {}


def _systemd_epoch(ts: str) -> float | None:
    """'Sat 2026-09-05 18:20:32 UTC' -> epoch. Empty (never ran) -> None."""
    m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ?(\S*)", ts or "")
    if not m:
        return None
    naive = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    if m.group(2) in ("UTC", "GMT", "Z"):
        return naive.replace(tzinfo=dt.timezone.utc).timestamp()
    return time.mktime(naive.timetuple())


def _fleet_sync_state(state: Path, now: float) -> tuple[bool, str]:
    """Did the fleet sync run recently and succeed? Never vacuous.

    Until 2026-09-05 this looked for a FAIL line in the last three lines of
    whatever log was there, so a host with no log passed, and a host whose
    timer had stopped kept passing on its last report. Where the sync is a
    systemd unit, its own Result and exit time are the record; the log is
    the fallback, and "nothing to read" is a failure, not a pass.
    """
    unit = _unit_show(FLEET_SYNC_UNIT)
    if unit:
        exit_at = _systemd_epoch(unit.get("ExecMainExitTimestamp", ""))
        if exit_at is None:
            return False, "unit never ran"
        age_h = (now - exit_at) / 3600
        if unit.get("Result") != "success":
            return False, f"last run {unit.get('Result') or 'unknown'} ({age_h:.0f}h ago)"
        if age_h > FLEET_SYNC_MAX_AGE_H:
            return False, f"last run {age_h:.0f}h ago (timer stopped?)"
        return True, "ok"
    fs = _lines(state / "fleet-sync.log")
    if not fs:
        return False, "never observed (no unit, no log)"
    age_h = _age_hours(state / "fleet-sync.log", now)
    if age_h is None or age_h > FLEET_SYNC_MAX_AGE_H:
        return False, f"log {age_h:.0f}h old" if age_h is not None else "log unreadable"
    bad = [l for l in fs if l.startswith("FAIL")]
    return (not bad), (bad[-1][:70] if bad else "ok")


#: The off-box prober's address, as it appears in the daemon's request log.
PROBER_IP = os.environ.get("COS_PROBER_IP", "100.101.159.42")
PROBE_MINUTES = int(os.environ.get("COS_PROBE_MINUTES", "15"))


def r5_reachable(state: Path, day: str, now: float | None = None) -> tuple[bool, str]:
    """Measured ON THE BOX from the prober's own hits.

    The probe runs on another host and writes its log there; reading that
    log here passed vacuously ("no probe log on this host") on 2026-09-05.
    The daemon logs every GET /health with the caller's address, so the
    number of hits from the prober today, against the number of probes the
    day has had time for, is the reachability record the box itself holds.
    The prober's own log stays the answer on the prober."""
    rows = [l for l in _lines(state / "cos-uptime.log") if l.startswith(day)]
    if rows:
        up = sum(1 for l in rows if " UP " in l)
        return up / len(rows) >= 0.995, f"{up}/{len(rows)} probes UP (prober log)"
    out = _journal(["-u", "datacored", "--since", f"{day} 00:00"])
    if not out:
        return False, "no probe log here and the daemon journal is unreadable"
    hits = sum(1 for l in out.splitlines() if "GET /health" in l and PROBER_IP in l)
    now = now or dt.datetime.now().timestamp()
    elapsed_min = max(0.0, (now - dt.datetime.fromisoformat(day).timestamp()) / 60)
    expected = int(elapsed_min // PROBE_MINUTES)
    # NOTHING DUE IS NOT A PASS. This returned True below four expected probes
    # whatever the hits, and "too early to judge" is an answer, not a pass.
    if expected == 0:
        return False, f"{hits} probe hit(s); no probe due yet — cannot judge"
    # >= 99.5%, as the SLO page and this file's header say. The tolerance was
    # `max(1, expected // 200)`, which always forgave one miss: 3/4 (75%) and
    # 95/96 (98.96%) both passed. expected // 200 is the 0.5% allowance itself,
    # rounded down, so hits >= expected - expected // 200 implies
    # hits / expected >= 0.995 (DatacoreSpec/Detectors.lean, `r5_ok_sound`).
    # A healthy prober on a fixed 15-minute cadence always lands at least
    # floor(elapsed / 15) hits, so no extra slack is needed for phase.
    ok = hits >= expected - expected // 200
    return ok, f"{hits}/{expected} probe hits from {PROBER_IP} today"


_R6_FAIL = re.compile(r"^\S+ (?:\S+ )?(?:\[[^\]]+\] )?FAIL\b")


def r6_rebuildable(cos: Path, day: str) -> tuple[bool, str]:
    rows = _lines(cos / "verify-daily.log")
    today = [l for l in rows if l.startswith(day)]
    if not today:
        return False, "no --verify run logged today"
    # A FAIL line is one whose VERDICT is FAIL — `<date> [cos-setup] FAIL <check>`
    # or `<date> <time> FAIL <check>` — never an OK line that quotes the word.
    # 2026-09-07: --verify printed "OK  scoreboard: 2026-09-06 FAIL streak=0 …"
    # (yesterday's line, quoted as a check), the substring match counted it,
    # and R6 failed today's scoreboard on a day every check had passed.
    fails = [l for l in today if _R6_FAIL.match(l)]
    return not fails, (f"{len(fails)} FAIL line(s)" if fails else "0 FAIL")



def principal_rows(root: Path, now: float | None = None, hours: float = 26.0) -> list[dict]:
    """One row per agent principal (product description: a scoreboard row per
    principal). Its health is what its own machine's verifier attested to the
    ledger (job_verify -> metric.attest job.verify) within the window: how many
    contracts passed, how many failed, how long ago. A principal nobody has
    heard from is a row that says so, not a missing row."""
    from actor_identity import principals
    from job_attestations import latest_jobs
    from ledger.log import CorruptLogError
    now = time.time() if now is None else now
    registry = root / ".datacore" / "registry" / "principals.yaml"
    ps = principals(registry)
    error = False
    try:
        latest = latest_jobs(root, now, registry=registry)
    except (OSError, CorruptLogError):
        latest, error = {}, True
    rows = []
    for name, p in ps.items():
        if str(p.get("kind") or "") != "agent":
            continue
        if error:
            rows.append({"principal": name, "ok": None, "note": "verification evidence unreadable or invalid"})
            continue
        jobs = {job: rec for job, rec in latest.get(name, {}).items() if now - rec.timestamp <= hours * 3600}
        if not jobs:
            rows.append({"principal": name, "ok": None, "note": f"not heard from in {hours:.0f}h"})
            continue
        failing = sorted(j for j, r in jobs.items() if not r.ok)
        age_h = (now - max(r.timestamp for r in jobs.values())) / 3600
        rows.append({"principal": name, "ok": not failing,
                     "note": f"{len(jobs) - len(failing)}/{len(jobs)} contracts verified {age_h:.0f}h ago" + (f"; failing: {', '.join(failing)[:80]}" if failing else "")})
    return rows


def principal_lines(rows: list[dict]) -> list[str]:
    return [f"  principal={r['principal']} {'ok' if r['ok'] else ('n-a' if r['ok'] is None else 'FAIL')} | {r['note']}" for r in rows]


def level(streak: int) -> int:
    return 5 if streak >= 30 else 4 if streak >= 7 else 3


_DAY_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (PASS|FAIL) streak=(\d+) level=(\d+)")


def _chain(prev: str | None, line: str) -> str:
    """`line` with streak/level recomputed from the line for the day before."""
    m = _DAY_LINE.match(line)
    if not m:
        return line
    day, verdict = m.group(1), m.group(2)
    streak = 0
    if verdict == "PASS":
        base = 0
        p = _DAY_LINE.match(prev or "")
        if p and p.group(2) == "PASS" and \
                p.group(1) == (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat():
            base = int(p.group(3))
        streak = base + 1
    return (f"{day} {verdict} streak={streak} level={level(streak)}" + line[m.end():])


def write_day_lines(existing: list[str], new: str) -> list[str]:
    """The log after writing `new`: one line per day, IN DATE ORDER, and every
    day after `new` re-chained.

    The streak is chained from yesterday's line, so the log's order and its
    later lines both matter. A `--date` backfill used to be appended at the
    end: the next run read it as "the last line", found it was not yesterday,
    and reset the streak; `tail -1` in cos-server-setup read it as today. And a
    backfilled FAIL left every later day carrying the streak it had before, so
    a level-5 streak survived a failing day (DatacoreSpec/Detectors.lean,
    `rechain_step`). A normal run writes the newest day, re-chains nothing and
    leaves history byte-identical.
    """
    m = _DAY_LINE.match(new)
    day = m.group(1) if m else new.split(" ", 1)[0]
    kept = [l for l in existing if not l.startswith(day + " ")]
    dated = sorted((l for l in kept if _DAY_LINE.match(l)), key=lambda l: l[:10])
    other = [l for l in kept if not _DAY_LINE.match(l)]
    before = [l for l in dated if l[:10] < day]
    after = [l for l in dated if l[:10] > day]
    out = before + [new]
    for l in after:
        out.append(_chain(out[-1], l))
    return other + out


def compute(day: str, state: Path, cos: Path, now: float | None = None) -> dict:
    now = now or dt.datetime.now().timestamp()
    checks = {
        "R1": r1_delivered(state), "R2": r2_loud(cos, day), "R3": r3_unattended(cos, day),
        "R4": r4_data_safe(cos, state, now), "R5": r5_reachable(state, day, now), "R6": r6_rebuildable(cos, day),
    }
    passed = all(ok for ok, _ in checks.values())
    log = state / "reliability-scoreboard.log"
    # YESTERDAY BY DATE, not the file's last line: a backfilled day sits
    # wherever it was written, and the last line is not necessarily yesterday.
    yesterday = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
    y = [l for l in _lines(log) if l.startswith(yesterday + " ") and _DAY_LINE.match(l)]
    streak = 0
    if y:
        m = _DAY_LINE.match(y[-1])
        if m.group(2) == "PASS":
            streak = int(m.group(3))
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
    prow = principal_rows(Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data"))))
    for ln in principal_lines(prow):
        print(ln)
    if not a.no_write:
        state.mkdir(parents=True, exist_ok=True)
        log = state / "reliability-scoreboard.log"
        log.write_text("\n".join(write_day_lines(_lines(log), out)) + "\n")
    print(json.dumps(r, indent=2) if a.json else out)
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
