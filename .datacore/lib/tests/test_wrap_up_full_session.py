"""/wrap-up accounts for the whole session, not just what survived the last compaction.

On 2026-11-01 a wrap-up of a nine-day session was built from the latest
compaction summary alone. It reported two days of work and left the three days
before them with no journal entry at all. The owner had to ask "did you analyze
full session?". The rule was already in memory twice; memory did not stop it.

So the report now refuses unless the narrative accounts for every day the owner
spoke in this session, and for every compacted stretch.

## What would have to break for these to fail

 - the archive stops recording which days the owner wrote on, or counts a
   compaction summary as the owner typing → the summarize tests fail.
 - a narrative whose phases skip a day the owner wrote on renders anyway →
   the refusal test fails.
 - a compacted single-day session renders without phases → fails.
 - phases that do cover the session are refused, or are not shown → fails.
"""
from __future__ import annotations

import json
import time

import pytest

import session_archive as sa
import wrap_up_report as wr
from tests.test_wrap_up_report import MECH, narrative


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _user(ts, text, **extra):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}, **extra}


def test_archive_records_owner_days_and_compactions(tmp_path):
    t = tmp_path / "s.jsonl"
    t.write_text("\n".join(json.dumps(r) for r in [
        _user("2026-10-24T10:21:00Z", "start the multi-tenant feature"),
        _user("2026-10-25T07:11:00Z", "status"),
        _user("2026-10-28T08:00:00Z", "This session is being continued from a previous "
              "conversation that ran out of context.", isCompactSummary=True),
        _user("2026-10-28T08:47:00Z", "<system-reminder>hook</system-reminder>"),
        _user("2026-10-28T08:48:00Z", "is this feature ready?"),
        _user("2026-11-01T13:38:00Z", "wrap up"),
    ]) + "\n")
    s = sa.summarize(t, [])
    assert s["owner_days"] == ["2026-10-24", "2026-10-25", "2026-10-28", "2026-11-01"]
    assert s["compactions"] == 1
    assert s["user_turns"] == 4   # the compaction summary is not the owner typing


def _mech(days, compactions=0):
    return dict(MECH, archive_meta=dict(MECH["archive_meta"], owner_days=days,
                                        compactions=compactions))


def test_phases_that_skip_an_owner_day_refuse():
    mech = _mech(["2026-10-24", "2026-10-28", "2026-10-29", "2026-11-01"], compactions=6)
    n = narrative(phases=[{"dates": "2026-10-31..2026-11-01", "summary": "PR sweep"}])
    with pytest.raises(wr.ReportInputError) as e:
        wr.render(n, mech)
    assert "owner days not covered: 2026-10-24, 2026-10-28, 2026-10-29" in str(e.value)


def test_multi_day_session_without_phases_refuses():
    with pytest.raises(wr.ReportInputError, match="phases"):
        wr.render(narrative(), _mech(["2026-10-30", "2026-10-31"]))


def test_compacted_single_day_session_without_phases_refuses():
    with pytest.raises(wr.ReportInputError, match="phases"):
        wr.render(narrative(), _mech(["2026-11-01"], compactions=1))


def test_phases_covering_every_owner_day_render():
    mech = _mech(["2026-10-24", "2026-10-26", "2026-10-28", "2026-11-01"], compactions=6)
    phases = [{"dates": "2026-10-24..2026-10-26", "summary": "Build"},
              {"dates": "2026-10-28", "summary": "Readiness"},
              {"dates": "2026-10-31..2026-11-01", "summary": "PR sweep"}]
    out = wr.render(narrative(phases=phases), mech)
    assert "Phases (4 owner days, 6 compactions):" in out
    assert "2026-10-24..2026-10-26: Build" in out and "2026-10-28: Readiness" in out


def test_one_uncompacted_day_needs_no_phases():
    assert "Phases" not in wr.render(narrative(), _mech(["2026-09-25"]))


def test_malformed_phase_dates_refuse():
    with pytest.raises(wr.ReportInputError, match="phases"):
        wr.render(narrative(phases=[{"dates": "last week", "summary": "x"}]),
                  _mech(["2026-10-30", "2026-10-31"]))
