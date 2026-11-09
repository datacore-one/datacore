"""The fleet simulator stops itself before it fills the disk (Phase 5A, break 6).

2026-10-03: the 7-night rogue-agent run filled the Mac's disk to 218 MB free
(from about 14 GB) and died with an I/O error on its own marker file; Docker
could no longer write its metadata and the container could be neither killed
nor removed. The lesson the run report wrote down: check free host disk first,
and stop cleanly below a floor (5 GB), naming why. These tests drive the real
`_docker` entry point with only `docker`, the seed preparation and the disk
reading replaced, so nothing is built and no container runs.
"""
from __future__ import annotations

from collections import namedtuple
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
import fleet_week_sim as sim  # noqa: E402

Usage = namedtuple('Usage', 'total used free')
GB = 1_000_000_000


def _args(tmp_path, **kw):
    base = dict(workdir=str(tmp_path / 'work'), out=str(tmp_path / 'out'), selftest=False, days=7,
                no_build=True, evals='off', min_interval=7200, harsh=False, rogue=True,
                no_faults=False, faults=None, min_free_gb=5.0)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def docker(monkeypatch):
    calls = {'prepare': 0, 'run': [], 'popen': [], 'killed': []}
    monkeypatch.setattr(sim, 'prepare', lambda src, seed: calls.__setitem__('prepare', calls['prepare'] + 1))

    def fake_run(cmd, *a, **kw):
        calls['run'].append(cmd)
        if cmd[:2] == ['docker', 'kill']:
            calls['killed'].append(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, '', '')
    monkeypatch.setattr(sim.subprocess, 'run', fake_run)

    class FakePopen:
        def __init__(self, cmd, *a, **kw):
            calls['popen'].append(cmd)
            self.waits = 0
            self.returncode = None

        def wait(self, timeout=None):
            self.waits += 1
            if calls['killed']:
                self.returncode = 137
                return 137
            raise subprocess.TimeoutExpired('docker', timeout)

        def poll(self):
            return self.returncode
    monkeypatch.setattr(sim.subprocess, 'Popen', FakePopen)
    return calls


def test_a_run_does_not_start_below_the_floor(tmp_path, monkeypatch, docker, capsys):
    monkeypatch.setattr(sim.shutil, 'disk_usage', lambda p: Usage(500 * GB, 499 * GB, 1 * GB))
    rc = sim._docker(_args(tmp_path))
    assert rc != 0
    assert docker['prepare'] == 0 and docker['popen'] == [] and not any(
        c[:2] == ['docker', 'run'] for c in docker['run'])
    said = capsys.readouterr().out
    assert 'free' in said and '5' in said


def test_a_running_week_is_stopped_cleanly_when_space_runs_low(tmp_path, monkeypatch, docker, capsys):
    readings = iter([50 * GB, 50 * GB, 40 * GB, 3 * GB])
    last = {'v': 50 * GB}

    def usage(path):
        last['v'] = next(readings, last['v'])
        return Usage(500 * GB, 500 * GB - last['v'], last['v'])
    monkeypatch.setattr(sim.shutil, 'disk_usage', usage)
    monkeypatch.setattr(sim, 'DISK_POLL_SECONDS', 0.01)
    rc = sim._docker(_args(tmp_path))
    assert rc != 0
    assert docker['killed'], 'the container was not stopped'
    stopped = (tmp_path / 'out' / 'STOPPED.txt').read_text()
    assert 'free' in stopped and 'GB' in stopped
    assert 'stopped' in capsys.readouterr().out.lower()


def test_a_week_with_room_runs_to_the_end(tmp_path, monkeypatch, docker):
    monkeypatch.setattr(sim.shutil, 'disk_usage', lambda p: Usage(500 * GB, 100 * GB, 400 * GB))
    monkeypatch.setattr(sim, 'DISK_POLL_SECONDS', 0.01)

    class Done:
        def __init__(self, cmd, *a, **kw):
            docker['popen'].append(cmd)
            self.returncode = 0

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0
    monkeypatch.setattr(sim.subprocess, 'Popen', Done)
    assert sim._docker(_args(tmp_path)) == 0
    assert docker['killed'] == []


def test_the_week_itself_checks_the_disk_between_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(sim.shutil, 'disk_usage', lambda p: Usage(10 * GB, 9 * GB, 1 * GB))
    reason = sim.disk_low([tmp_path], 5.0)
    assert reason and '1.0 GB free' in reason and '5' in reason
    monkeypatch.setattr(sim.shutil, 'disk_usage', lambda p: Usage(10 * GB, 1 * GB, 9 * GB))
    assert sim.disk_low([tmp_path], 5.0) is None
