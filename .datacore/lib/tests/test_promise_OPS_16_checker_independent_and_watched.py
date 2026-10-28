"""OPS-16: The check that watches my jobs never depends on the jobs it watches, and
every machine is also judged from another machine: if a machine's own checker has not
run for a day, another machine tells me.

Kind: deterministic.

Evidence guarded (log review 2026-10-28, findings-mac S1-S3):
- visitor_join.py:338 counts a join as converged only when the phase-1 cycle exits 0,
  and :363-365 runs the machine's `trigger: join` duties ONLY after a converged join.
  The mac's job checker is one of those duties (manifest.yaml mac-job-verify,
  `trigger: join`), so it hangs off a job it is meant to watch (mac-join). From
  2026-10-23 13:14Z the join never converged (an EditConflict in one space's ingest),
  15 duties stopped -- the checker among them -- and nothing alerted for five days:
  join.json's age is that job's only alarm (manifest.yaml:219-241), only job_verify
  reads it, and visitor_join.py:436-438 always exits 0.
- Hermes and plur-claw each judged only themselves and went quiet on 10-25 unreported:
  no machine's checker is judged from any other machine.

Graded, through the real code:
1. The REAL join tick (visitor_join.main --tick, the launchd entry point) is driven
   over a simulated waking day in which every join fails to converge. The mac's
   checker (the manifest job on `mac` that runs job_verify) must still run in that
   day -- unless it no longer hangs off the join at all. The cycle, the duty runner,
   the wake log and the clock are stand-ins; the manifest, `due()`, `join()`,
   `retry_failed()` and `run_due_gated()` are the product's.
2. (manifest contract) For every machine the manifest schedules, some job declared on
   ANOTHER machine reads checker evidence (job_verify output or its `job.verify`
   attestations) and is not confined to judging its own machine -- so a machine whose
   checker stopped is reported by someone else. Today no such job exists for any
   machine.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parents[1]
MANIFEST = LIB / "jobs" / "manifest.yaml"

import visitor_join as VJ  # noqa: E402


def _jobs() -> list[dict]:
    return yaml.safe_load(MANIFEST.read_text())["jobs"]


_CHECKER = re.compile(r"job_verify(_notify)?\.(py|sh)\b")


def _checker_of(machine: str) -> dict:
    mine = [j for j in _jobs() if j.get("machine") == machine and _CHECKER.search(str(j.get("cmd", "")))]
    assert mine, f"the manifest declares no job that runs job_verify on {machine!r}"
    return mine[0]


def test_the_macs_checker_runs_on_a_day_the_join_never_converges(tmp_path, monkeypatch):
    checker = _checker_of("mac")
    st = tmp_path / "state"
    st.mkdir()
    for attr, name in (("RECORD", "join.json"), ("ATTEMPT", "join-attempt.json"),
                       ("LOG", "join.log"), ("DUTIES", "join-duties.json")):
        monkeypatch.setattr(VJ, attr, st / name)

    clock = {"t": 1_792_800_000.0}               # 2026-10-23, a weekday morning
    monkeypatch.setattr(time, "time", lambda: clock["t"])
    monkeypatch.setattr(VJ, "_clock", lambda: clock["t"])

    from jobs import awake
    import actor_identity
    monkeypatch.setattr(awake, "in_dark_wake", lambda **k: False)     # the lid is open all day
    monkeypatch.setattr(awake, "last_full_wake", lambda **k: clock["start"])
    monkeypatch.setattr(awake, "awake_age", lambda mtime, *a, **k: clock["t"] - mtime)
    monkeypatch.setattr(actor_identity, "this_actor", lambda *a, **k: "mac")

    # The phase-1 cycle fails as it did from 10-23: one space's ingest hit an edit conflict.
    monkeypatch.setattr(VJ, "converge", lambda: (1, "9-practice FAILED: EditConflict: concurrent edit"))
    monkeypatch.setattr(VJ, "measure", lambda **k: {"ahead": 0, "behind": 0, "gap": 0,
                                                     "blocked": [], "errors": []})
    ran: list[str] = []
    monkeypatch.setattr(VJ, "run_duty", lambda name: ran.append(name) or
                        {"rc": 0, "at": clock["t"], "seconds": 1.0, "last": "ok"})

    clock["start"] = clock["t"] - 60
    monkeypatch.setattr(sys, "argv", ["visitor_join.py", "--tick"])
    # launchd ticks every 5 minutes while the machine is awake; a waking day of 14 hours.
    end = clock["t"] + 14 * 3600
    while clock["t"] < end:
        assert VJ.main() == 0
        clock["t"] += 300

    hangs_off_join = checker.get("trigger") in ("join", "arrival")
    assert checker["name"] in ran or not hangs_off_join, (
        f"the mac's job checker ({checker['name']}, trigger: {checker.get('trigger')}) never ran in a "
        f"14-hour waking day because every join failed to converge: the checker depends on mac-join, "
        f"a job it is meant to watch (duties run: {sorted(set(ran)) or 'none'})")


_EVIDENCE = re.compile(r"job_verify|job\.verify|job_attestations|checker|verifier", re.I)


def test_every_machine_is_judged_from_another_machine():
    jobs = _jobs()
    machines = sorted({j["machine"] for j in jobs})
    unwatched = []
    for m in machines:
        watchers = []
        for j in jobs:
            if j["machine"] == m:
                continue
            text = " ".join([str(j.get("cmd", ""))] + [f"{a.get('path', '')} {a.get('arg', '')}"
                                                      for a in j.get("artifacts") or []])
            if not _EVIDENCE.search(text):
                continue
            own = re.search(r"--machine[ =](\S+)", str(j.get("cmd", "")))
            if own and own.group(1) == j["machine"]:
                continue                       # a machine's own checker judges only itself
            watchers.append(j["name"])
        if not watchers:
            unwatched.append(m)
    assert not unwatched, (
        f"no machine judges the checker of {', '.join(unwatched)}: if one of their checkers stops "
        f"for a day, no other machine reads that and tells the owner (every job_verify run judges "
        f"only its own machine)")
