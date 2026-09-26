"""LED-8: Routine measurements are kept in a separate log per space, still
count when the history is read, and never slow task records.

Kind: deterministic. Real producers into a tmp space: `job_verify.main`
(metric.attest job.verify, one per job) and `ledger_attest.attest`
(artifact.attest); a task record via `EventLog.append("item.create")`.

Owner decision (2026-09-26): telemetry (metric.attest / artifact.attest)
belongs in a separate per-space log, folded in at read time.

Promise, as evals:
  * separate: no task log (`<space>/.datacore/events/*.jsonl`) holds a
    telemetry event, and the telemetry is still stored somewhere in the space;
  * still counts: `read_events(space)` returns the telemetry, and
    `job_attestations.latest_jobs` (what the absence gate reads) sees the
    job.verify verdicts;
  * never slows task records: appending a task record parses no more events
    in a space with 300 measurements than in one with none.

Seeded failure: today's layout -- job_verify and ledger_attest append to the
same `<actor>.jsonl` as task records (LS-14: 71-97% of item-log events are
telemetry; 6-meridian carries 21k trade attests in its task log).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

import actor_identity
import job_verify
import ledger.log as ledger_log
import ledger_attest
from ledger.log import EventLog, read_events

ACTOR = "tester"
TELEMETRY = {"metric.attest", "artifact.attest"}
N = 300


@pytest.fixture
def principals(tmp_path, monkeypatch):
    p = tmp_path / "principals.yaml"
    p.write_text(f"principals:\n  tester:\n    kind: agent\n    writes_as: [{ACTOR}]\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", p)
    monkeypatch.setenv("DATACORE_ACTOR", ACTOR)
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    return p


def _space(root: Path, name: str) -> Path:
    space = root / name
    (space / ".datacore" / "events").mkdir(parents=True)
    return space


def _measure(tmp_path: Path, space: Path, jobs: int) -> None:
    artifact = tmp_path / "artifact.log"
    artifact.write_text("done")
    manifest = tmp_path / f"manifest-{space.name}.yaml"
    manifest.write_text(yaml.safe_dump({"version": 1, "jobs": [
        {"name": f"job-{i}", "machine": "mac", "schedule": "0 3 * * *", "cmd": "true",
         "artifacts": [{"path": str(artifact)}]} for i in range(jobs)]}, sort_keys=False))
    try:
        job_verify.main(["--machine", "mac", "--manifest", str(manifest), "--space", str(space)])
    except SystemExit as exc:
        assert not exc.code, f"job_verify failed: {exc.code}"


def _task_log_types(space: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for p in sorted((space / ".datacore" / "events").glob("*.jsonl")):
        out[p.name] = {e.type for e in ledger_log._parse_log_bytes(p.read_bytes(), p)[0]}
    return out


def test_measurements_are_not_kept_in_the_task_logs(tmp_path, principals):
    root = tmp_path / "Data"
    space = _space(root, "9-fixture")
    EventLog(space, ACTOR).append("item.create", {"id": "t1", "title": "task", "state": "NEXT"})
    _measure(tmp_path, space, 3)
    assert ledger_attest.attest("x.post", ref="123", detail="hello", space=str(space))
    EventLog(space, ACTOR).append("item.create", {"id": "t2", "title": "task", "state": "NEXT"})

    mixed = {name: sorted(types & TELEMETRY) for name, types in _task_log_types(space).items()
             if types & TELEMETRY}
    assert not mixed, f"telemetry kept in the task logs: {mixed}"


def test_measurements_still_count_when_the_history_is_read(tmp_path, principals):
    root = tmp_path / "Data"
    space = _space(root, "9-fixture")
    EventLog(space, ACTOR).append("item.create", {"id": "t1", "title": "task", "state": "NEXT"})
    _measure(tmp_path, space, 2)
    assert ledger_attest.attest("x.post", ref="123", detail="hello", space=str(space))

    types = [e.type for e in read_events(space)]
    assert types.count("metric.attest") == 2 and types.count("artifact.attest") == 1, types
    assert "item.create" in types

    from job_attestations import latest_jobs
    seen = latest_jobs(root, time.time() + 1, registry=principals)
    assert set(seen.get("tester", {})) == {"job-0", "job-1"}, seen
    assert all(a.ok for a in seen["tester"].values())


def _parses_for_one_task_append(space: Path, monkeypatch) -> int:
    calls = {"n": 0}
    real = ledger_log.from_line

    def counting(line):
        calls["n"] += 1
        return real(line)
    monkeypatch.setattr(ledger_log, "from_line", counting)
    EventLog(space, ACTOR).append("item.create", {"id": "late", "title": "task", "state": "NEXT"})
    monkeypatch.setattr(ledger_log, "from_line", real)
    return calls["n"]


def test_measurements_never_slow_a_task_record(tmp_path, principals, monkeypatch):
    root = tmp_path / "Data"
    quiet, busy = _space(root, "1-quiet"), _space(root, "2-busy")
    for space in (quiet, busy):
        EventLog(space, ACTOR).append("item.create", {"id": "t1", "title": "task", "state": "NEXT"})
    _measure(tmp_path, busy, N)
    assert sum(1 for e in read_events(busy) if e.type == "metric.attest") == N

    base = _parses_for_one_task_append(quiet, monkeypatch)
    loaded = _parses_for_one_task_append(busy, monkeypatch)
    assert loaded <= base + 5, (
        f"appending one task record parsed {loaded} events with {N} measurements in the space, "
        f"{base} without: telemetry volume slows task records")
