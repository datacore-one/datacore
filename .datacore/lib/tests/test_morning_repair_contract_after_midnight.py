"""The morning-repair contracts hold between midnight and the job's own run.

morning-repair-sweep runs at 02:00 and -recheck at 03:30 (box, UTC). Their
contracts named `{today}`'s file, and job_verify runs every hour: from 00:00
until the job's run, today's file cannot exist yet, so both jobs failed every
night. On 2026-10-02 that failure was handed to the box's repair agent
(autofix-morning-repair-sweep-20261002, -recheck-20261002), which dead-lettered
them and listed both under "repairs need a person" -- for jobs that ran fine
(winston: 2026-10-02.json written 02:03, repairs.json 03:31).

Freshness (max_age_hours: 26) is the liveness signal; the date in the name is
not. The same remedy as mac-agent-stream-rsync in jobs/checks.expand_path: the
newest matching file, judged by its age and content.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from jobs.checks import run_check  # noqa: E402
from jobs.manifest import load_manifest  # noqa: E402

MANIFEST = LIB / "jobs" / "manifest.yaml"


def _job(name):
    jobs = {j.name: j for j in load_manifest(MANIFEST)}
    return jobs[name]


def _write(path: Path, data: dict, at: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    os.utime(path, (at, at))


def _night(hour: int) -> tuple[float, str]:
    """`hour`:00 local today, and yesterday's date."""
    today = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    return today.timestamp(), (today - timedelta(days=1)).strftime("%Y-%m-%d")


def test_sweep_contract_holds_at_one_in_the_morning(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    now, yesterday = _night(1)
    _write(tmp_path / ".datacore/state/morning-repair" / f"{yesterday}.json",
           {"swept_at": now - 23 * 3600, "findings": []}, now - 23 * 3600)
    errors = [e for a in _job("morning-repair-sweep").artifacts for e in run_check(a, now=now)]
    assert errors == [], errors


def test_recheck_contract_holds_at_three_in_the_morning(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    now, yesterday = _night(3)
    _write(tmp_path / ".datacore/cos/fragments" / yesterday / "repairs.json",
           {"repaired": [], "still_failing": []}, now - 23.5 * 3600)
    errors = [e for a in _job("morning-repair-recheck").artifacts for e in run_check(a, now=now)]
    assert errors == [], errors


def test_a_sweep_that_stopped_running_still_fails(tmp_path, monkeypatch):
    """The boundary: a glob must not pass on an old file."""
    monkeypatch.setenv("HOME", str(tmp_path))
    now, _ = _night(9)
    old = now - 3 * 86400
    _write(tmp_path / ".datacore/state/morning-repair" / "2020-01-01.json",
           {"swept_at": old, "findings": []}, old)
    errors = [e for a in _job("morning-repair-sweep").artifacts for e in run_check(a, now=now)]
    assert errors, "a three-day-old sweep record passed the contract"
