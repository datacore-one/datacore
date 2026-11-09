"""Plan phase C (owner, 2026-10-31): the task fold never reads telemetry logs.

Telemetry (routine measurements: metric.attest, artifact.attest, agent.checkin)
has lived in its own per-writer files since LED-8, and `fold()` ignores those
types. But every task reader still parsed them through `read_events`. In
1-datafund that was 155k telemetry lines against 26k task lines. LED-8's
promise is untouched: `read_events` still returns telemetry for the readers
that need it (job checker, presence, scoreboard).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import actor_identity
import ledger.log as ledger_log
from ledger.fold import fold
from ledger.log import EventLog, read_events

ACTOR = "tester"


@pytest.fixture
def space(tmp_path, monkeypatch):
    p = tmp_path / "principals.yaml"
    p.write_text(f"principals:\n  tester:\n    kind: agent\n    writes_as: [{ACTOR}]\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", p)
    monkeypatch.setenv("DATACORE_ACTOR", ACTOR)
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    s = tmp_path / "Data" / "9-fixture"
    (s / ".datacore" / "events").mkdir(parents=True)
    log = EventLog(s, ACTOR)
    log.append("item.create", {"id": "t1", "title": "one", "state": "NEXT"})
    for i in range(5):
        log.append("metric.attest", {"metric": "job.verify", "job": f"j{i}", "ok": True})
    log.append("agent.checkin", {"host": "h"})
    log.append("item.create", {"id": "t2", "title": "two", "state": "NEXT"})
    assert list((s / ".datacore" / "telemetry").glob("*.jsonl")), "fixture wrote no telemetry"
    return s


def _telemetry_files_read(monkeypatch) -> list[str]:
    seen: list[str] = []
    real = ledger_log._parse_log_bytes

    def spy(raw, path):
        if Path(path).parent.name == ledger_log.TELEMETRY_DIR:
            seen.append(str(path))
        return real(raw, path)
    monkeypatch.setattr(ledger_log, "_parse_log_bytes", spy)
    return seen


def test_the_task_reader_returns_task_events_only(space):
    from ledger.log import read_task_events
    task = read_task_events(space)
    assert [e.type for e in task] == ["item.create", "item.create"]
    assert all(not e.log.endswith(ledger_log.TELEMETRY_SUFFIX) for e in task)
    assert [e.hlc for e in task] == sorted(e.hlc for e in task)


def test_read_events_still_returns_telemetry(space):
    types = [e.type for e in read_events(space)]
    assert types.count("metric.attest") == 5 and "agent.checkin" in types


def test_the_state_of_a_space_reads_no_telemetry_file(space, monkeypatch):
    from ledger.hub import authoritative_state
    seen = _telemetry_files_read(monkeypatch)
    state = authoritative_state(space)
    assert set(state.items) == {"t1", "t2"}
    assert not seen, f"the task fold read telemetry: {seen}"


def test_the_state_root_is_the_same_without_telemetry(space):
    from ledger.hub import authoritative_state
    assert authoritative_state(space).state_root() == fold(read_events(space)).state_root()


def test_materialize_reads_no_telemetry_file(space, monkeypatch):
    from briefing.actions import materialize
    seen = _telemetry_files_read(monkeypatch)
    materialize([{"text": "a new task"}], space, ACTOR)
    assert not seen, f"materialize read telemetry: {seen}"
