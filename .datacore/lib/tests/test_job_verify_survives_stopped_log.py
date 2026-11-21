"""A stopped event log stops the recording of verdicts, not the checking.

The chief of staff's machine, 2026-11-07 to 2026-11-21: its event log in the
system space was stopped (StaleLogError, the owner repairs it). job_verify
appends each verdict to that log, so every hourly run died on the first job,
766 times: no job on that machine was checked for two weeks, the recurrence
record froze, and ten jobs read as "failing for 16 to 51 days" on evidence
that old. The appends must stop (the ledger says so); the checks must not.
"""
from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import job_verify  # noqa: E402
from ledger.log import StaleLogError  # noqa: E402


class _Stopped:
    def __init__(self):
        self.calls = 0

    def append(self, *_a, **_k):
        self.calls += 1
        raise StaleLogError("winston.jsonl ends at seq 1 but this machine already wrote seq 2")


class _Fine:
    def __init__(self):
        self.events = []

    def append(self, kind, payload):
        self.events.append((kind, payload))


def test_a_stopped_log_is_reported_once_and_never_appended_to_again(capsys):
    job_verify._ledger_stopped.clear()
    log = _Stopped()
    after = job_verify._attest(log, "box-backup", True, [])
    assert after is None, "the checker must stop appending to a stopped log"
    assert job_verify._attest(after, "box-research", False, ["stale"]) is None
    assert log.calls == 1
    err = capsys.readouterr().err
    assert err.count("NOT being recorded") == 1 and "the owner" in err
    assert job_verify._ledger_stopped, "the run must remember it could not record"


def test_a_working_log_records_every_verdict():
    job_verify._ledger_stopped.clear()
    log = _Fine()
    assert job_verify._attest(log, "box-backup", False, ["missing"]) is log
    assert log.events == [("metric.attest", {"metric": "job.verify", "job": "box-backup",
                                             "ok": False, "failures": ["missing"]})]
    assert not job_verify._ledger_stopped
