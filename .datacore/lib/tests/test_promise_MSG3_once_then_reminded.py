"""MSG-3: I get each problem once. The same problem is not re-sent until its cool-down ends, but a problem that keeps going is reminded.

Kind: deterministic. Drives the real `job_verify._dispatch_alert` (artifact
signature, the recurrence counter in jobs/recurrence.py, should_alert,
note_alerted) the way the scheduler does -- one look every 30 minutes -- over
several simulated days, on a fake clock, with state in a tmp DATACORE_STATE.
Stood in for: the Telegram send (captured), and delegation (refused, so the
problem is the operator's, which is when messages are sent at all).

Seeded failure: one failing job seen 48 times a day for four days, in three
shapes -- (a) the artifact is bad and never rewritten; (b) the artifact goes
stale (max_age_hours 26) because the producer died; (c) the producer rewrites
the same bad artifact three times a day. The promise holds when each shape
yields at most one message per day (the cool-down) and at least one on every
later day (the reminder), and never 48 a day.
"""
from __future__ import annotations

import datetime as real_dt
import os
import sys
import types
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import job_verify as jv  # noqa: E402
from jobs import recurrence  # noqa: E402

START = 1_800_000_000.0 - (1_800_000_000.0 % 86400) + 3600   # 01:00 UTC on a fixed day
LOOK = 1800.0
DAYS = 4


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(recurrence, "STATE", recurrence._DEFAULT_STATE)
    clock = [START]

    class FakeDate(real_dt.date):
        @classmethod
        def today(cls):
            d = real_dt.datetime.fromtimestamp(clock[0], real_dt.timezone.utc).date()
            return cls(d.year, d.month, d.day)

    fake = types.SimpleNamespace(date=FakeDate, datetime=real_dt.datetime, timezone=real_dt.timezone)
    monkeypatch.setattr(recurrence, "datetime", fake)
    monkeypatch.setattr(jv, "_dt", fake)
    monkeypatch.setattr(jv.time, "time", lambda: clock[0])
    monkeypatch.setattr(jv, "_NO_EMIT", False)
    monkeypatch.setattr(jv, "_delegate_repair", lambda job, failures, rec: ("refused", "fixture"))
    monkeypatch.setattr(jv, "_file_task", lambda job, rec, failures: None)
    sent = []
    monkeypatch.setattr(jv, "_send_telegram", lambda msg: sent.append((clock[0], msg)) or True)
    return tmp_path, clock, sent


def _write(path, t):
    path.write_text("FAILED: 3 rows rejected\n")
    os.utime(path, (t, t))


def _simulate(world, *, max_age_hours=None, rewrites_per_day=0):
    tmp, clock, sent = world
    art = tmp / "producer.log"
    _write(art, START - 60)
    job = types.SimpleNamespace(
        name="fixture-job", machine=None, delegate=True,
        artifacts=[types.SimpleNamespace(path=str(art), max_age_hours=max_age_hours)])
    looks = int(86400 / LOOK)
    every = looks // rewrites_per_day if rewrites_per_day else None
    for day in range(DAYS):
        for k in range(looks):
            clock[0] = START + (day * looks + k) * LOOK
            if every and k % every == 0 and (day or k):
                _write(art, clock[0])
            jv._dispatch_alert("telegram", "fixture-job", ["artifact check failed"], job)
    per_day = [0] * DAYS
    for t, _ in sent:
        per_day[int((t - START) // 86400)] += 1
    return per_day


def _once_a_day_and_reminded(per_day, shape):
    assert per_day[0] >= 1, f'{shape}: the problem was never sent: {per_day}'
    assert all(n <= 1 for n in per_day), \
        f'{shape}: the same problem was re-sent inside its cool-down; messages per day {per_day}'
    assert all(n >= 1 for n in per_day[1:]), \
        f'{shape}: a problem that kept going was not reminded; messages per day {per_day}'


def test_a_bad_artifact_that_never_changes(world):
    _once_a_day_and_reminded(_simulate(world), 'unchanged bad artifact')


def test_a_producer_that_died_and_its_artifact_goes_stale(world):
    per_day = _simulate(world, max_age_hours=26)
    assert all(n <= 1 for n in per_day), f'stale artifact re-sent inside its cool-down: {per_day}'
    assert sum(per_day[1:]) >= 2, f'a producer dead for four days was not reminded: {per_day}'


def test_a_producer_that_keeps_writing_the_same_failure(world):
    _once_a_day_and_reminded(_simulate(world, rewrites_per_day=3), 'rewritten bad artifact')
