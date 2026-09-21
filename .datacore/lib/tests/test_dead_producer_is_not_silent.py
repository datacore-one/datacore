"""A producer that stops producing must get LOUDER, not quieter.

2026-09-18 -> 09-21: nightshift's overnight run was dead for two and a half
days. The verifier saw it every thirty minutes and wrote

    manifest-latest.yaml: stale (age 29.14h exceeds max_age_hours=26)
    manifest-latest.yaml: stale (age 35.20h ...)
    manifest-latest.yaml: stale (age 59.20h ...)
    alert withheld: nightshift-overnight failed on the same artifact already
    counted (1x); nothing new to report

The "same artifact" dedup is right for a BAD artifact: one broken file re-read
48 times is one failure. It is exactly wrong for a STALE one, where the file not
changing is the failure itself. So a dead producer got one alert, then silence,
and a streak pinned at 1 that could never reach DIP-0031's recurring threshold.

These tests pin the distinction.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import job_verify as jv  # noqa: E402

H = 3600.0
NOW = 1_800_000_000.0


def _job(path: Path, *, max_age_hours=None, machine=None):
    art = types.SimpleNamespace(path=str(path), max_age_hours=max_age_hours)
    return types.SimpleNamespace(artifacts=[art], machine=machine)


def _aged(path: Path, hours: float) -> Path:
    path.write_text("x")
    os.utime(path, (NOW - hours * H, NOW - hours * H))
    return path


@pytest.fixture
def rec(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "state"))
    from jobs import recurrence
    return recurrence


def test_a_bad_but_fresh_artifact_is_still_one_failure(tmp_path):
    """What the dedup was built for, and must keep doing."""
    job = _job(_aged(tmp_path / "out.log", 2), max_age_hours=26)
    assert jv._artifact_signature(job, now=NOW) == jv._artifact_signature(job, now=NOW + 1800)


def test_within_one_missed_period_a_stale_artifact_is_one_failure(tmp_path):
    job = _job(_aged(tmp_path / "manifest-latest.yaml", 29), max_age_hours=26)
    assert jv._artifact_signature(job, now=NOW) == jv._artifact_signature(job, now=NOW + 6 * H)


def test_every_further_missed_period_is_a_new_failure(tmp_path):
    job = _job(_aged(tmp_path / "manifest-latest.yaml", 29), max_age_hours=26)
    sigs = [jv._artifact_signature(job, now=NOW + extra * H) for extra in (0, 30, 56)]  # 29h, 59h, 85h
    assert len(set(sigs)) == 3, sigs


def test_a_producer_that_never_writes_is_a_new_failure_each_day(tmp_path, monkeypatch):
    job = _job(tmp_path / "never-written.log")

    class _Day:
        def __init__(self, iso): self._iso = iso
        def isoformat(self): return self._iso

    seen = []
    for iso in ("2026-09-18", "2026-09-19"):
        monkeypatch.setattr(jv._dt, "date", types.SimpleNamespace(today=lambda iso=iso: _Day(iso)))
        seen.append(jv._artifact_signature(job, now=NOW))
    assert seen[0] != seen[1] and "@missing" in seen[0]


def test_a_visitor_counts_missed_periods_in_awake_time(tmp_path, monkeypatch):
    """A laptop asleep for 60 wall-hours has not missed two 26-hour periods."""
    from jobs import awake
    monkeypatch.setattr(awake, "awake_age", lambda mtime, machine, **kw: 5 * H)
    job = _job(_aged(tmp_path / "join.json", 60), max_age_hours=26, machine="lap")
    assert "+missed" not in jv._artifact_signature(job, now=NOW)


def test_the_incident_replayed_reaches_recurring(tmp_path, rec):
    """29h, 35h, 59h, 85h -- the ages nightshift's verifier actually logged,
    plus the next day. Before the fix this ended at consecutive == 1."""
    job = _job(_aged(tmp_path / "manifest-latest.yaml", 29), max_age_hours=26)
    counts = []
    for extra in (0, 6, 30, 56):
        sig = jv._artifact_signature(job, now=NOW + extra * H)
        r = rec.record("nightshift-overnight", failed=True, artifact_sig=sig)
        counts.append((r["consecutive"], r["same_artifact"]))
    assert counts == [(1, False), (1, True), (2, False), (3, False)]
    assert r["recurring"] is True
