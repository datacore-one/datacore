"""MSG-13: a failure held back because "the repair path owns it" still reaches me, once,
when that path is paused, has given up, or has run past its window.

Split from CAD-6 on 2026-10-28: CAD-6 is green in the committed baseline (its cadence
evals pass), and this is a different path through job_verify, found in the 7-day fleet
log review (Winston: no backup for 3.7 days, 60 failures, 0 messages).

Today a failure the repairer cannot take on is never told to me.

Kind: deterministic. The real verifier (job_verify.main, with its recurrence and
dedup bookkeeping in the suite's tmp state) judges one job whose artifact keeps
failing the same way, once an hour for 30 simulated hours (time.time and the date the
verifier and its recurrence record read are a simulated clock). The repair hand-off
(jobs/autofix.delegate) is a stand-in returning what it returns in the two cases below;
the Telegram send is captured, never made.

Evidence guarded (findings-winston-state S12-S17, class C1):
- a failure first seen while repairs are PAUSED (the model usage limit) prints
  "repairs paused ...; operator not alerted" (job_verify.py, `state == "paused"`) and
  returns without alerting; every later run on the same artifact is withheld as
  "failed on the same artifact already counted" (job_verify.py ~536-545) because it was
  never alerted -- so it is never alerted at all. box-backup went 26.6 -> 89.6 h stale,
  60 failures, never sent.
- a failure handed to the repairer that then GAVE UP (dead-lettered; the breaker is
  open, which delegate reports as `escalate`) is never re-offered to the repair path:
  the same-artifact withhold returns first, so the owner is never told the repair
  failed.

Graded: when repairs stay paused, or the repairer has given up, for longer than the
24-hour window, the owner is alerted once -- not never, and not every hour.
"""
from __future__ import annotations

import datetime as _dt
import types
from pathlib import Path

import yaml

import job_verify

JOB = "sim-backup"
WINDOW_H = 24
HOURS = 30


def _clock(monkeypatch, start: float) -> dict:
    import time
    import jobs.recurrence as R
    clock = {"t": start}

    class _Date(_dt.date):
        @classmethod
        def today(cls):
            d = _dt.datetime.fromtimestamp(clock["t"]).date()
            return cls(d.year, d.month, d.day)

    shim = types.ModuleType("datetime")
    shim.__dict__.update(_dt.__dict__)
    shim.date = _Date
    monkeypatch.setattr(time, "time", lambda: clock["t"])
    monkeypatch.setattr(job_verify, "_dt", shim)
    monkeypatch.setattr(R, "datetime", shim)
    return clock


def _setup(tmp_path, monkeypatch):
    import jobs.recurrence as R
    monkeypatch.setattr(R, "STATE", tmp_path / "rec.json")
    artifact = tmp_path / "backup.log"
    artifact.write_text("backup FAILED: destination is a read-only file system\n")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({"version": 1, "jobs": [
        {"name": JOB, "machine": "box", "schedule": "30 2 * * *", "cmd": "true", "on_fail": "telegram",
         "artifacts": [{"path": str(artifact), "check": "regex", "arg": "^backup OK"}]}]}, sort_keys=False))
    space = tmp_path / "space"
    space.mkdir()
    monkeypatch.setenv("DATACORE_ACTOR", "test-actor")
    sent: list[str] = []
    monkeypatch.setattr(job_verify, "_send_telegram", lambda msg: sent.append(msg) or True)
    monkeypatch.setattr(job_verify, "_file_task", lambda *a, **k: None)
    monkeypatch.setattr(job_verify, "_close_task", lambda *a, **k: True)
    argv = ["--machine", "box", "--manifest", str(manifest), "--space", str(space)]
    return argv, sent


def _hourly(argv, clock, hours):
    for _ in range(hours):
        try:
            job_verify.main(argv)
        except SystemExit:
            pass
        clock["t"] += 3600


def test_a_failure_first_seen_while_repairs_are_paused_is_alerted_once_past_the_window(tmp_path, monkeypatch):
    clock = _clock(monkeypatch, 1_792_900_000.0)            # 2026-10-25, the evening the limit hit
    argv, sent = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(job_verify, "_delegate_repair", lambda job, failures, rec: (
        "paused", "repairs are paused: the last repair hit the model usage limit; nothing is filed until "
                  "it resets (about 6h)"))
    _hourly(argv, clock, HOURS)
    assert len(sent) == 1, (
        f"{JOB} failed on every hourly run for {HOURS}h while repairs stayed paused (window {WINDOW_H}h) and the "
        f"owner was alerted {len(sent)} time(s): the failure was first seen while paused, so every later run "
        f"was withheld as 'the same artifact already counted'")


def test_a_failure_the_repairer_gave_up_on_is_alerted_once_past_the_window(tmp_path, monkeypatch):
    clock = _clock(monkeypatch, 1_792_900_000.0)
    argv, sent = _setup(tmp_path, monkeypatch)
    calls = {"n": 0}

    def delegate(job, failures, rec):
        calls["n"] += 1
        if calls["n"] == 1:
            return "delegated", f"repair item filed for {job.name}"
        # three attempts dead-lettered: the breaker is open, no agent will try again
        return "escalate", (f"breaker open: 3 repair attempts for {job.name} dead-lettered; it needs a "
                            f"person (the owner) on {job.machine}")

    monkeypatch.setattr(job_verify, "_delegate_repair", delegate)
    _hourly(argv, clock, HOURS)
    assert len(sent) == 1, (
        f"the repairer gave up on {JOB} and over {HOURS}h the owner was alerted {len(sent)} time(s): after the "
        f"first run handed it over, every run was withheld as 'the same artifact already counted' "
        f"(the repair path was asked {calls['n']} time(s))")
