"""The daily distribution job: pull the space, collect, commit the snapshot as the
running agent, push. Written before the script (distribution plan, owner 2026-11-17:
"dayshift", "miles" commits the snapshots)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "lib" / "distribution_job.sh"


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _space(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    space = tmp_path / "space"
    subprocess.run(["git", "clone", "-q", str(remote), str(space)], check=True)
    metrics = space / "1-tracks" / "comms" / "metrics"
    metrics.mkdir(parents=True)
    (metrics / "collector.json").write_text('{"out_dir": "."}\n')
    (space / "unrelated.txt").write_text("someone else's work\n")
    _git("add", "1-tracks", cwd=space)
    _git("-c", "user.name=Owner", "-c", "user.email=o@example.com", "commit", "-q", "-m", "seed", cwd=space)
    _git("push", "-q", "origin", "main", cwd=space)
    return remote, space


def _fake_collector(tmp_path):
    fake = tmp_path / "fake_collector.py"
    fake.write_text(
        "import json, sys, datetime, pathlib\n"
        "conf = pathlib.Path(sys.argv[sys.argv.index('--config') + 1])\n"
        "(conf.parent / (datetime.date.today().isoformat() + '.json')).write_text(json.dumps({'ok': 1}))\n"
    )
    return fake


def _run(space, fake, **env):
    e = dict(os.environ, COLLECTOR=str(fake), GIT_AUTHOR_NAME="Miles", GIT_AUTHOR_EMAIL="miles@example.com",
             GIT_COMMITTER_NAME="Miles", GIT_COMMITTER_EMAIL="miles@example.com", **env)
    return subprocess.run(["bash", str(SCRIPT), str(space), "1-tracks/comms/metrics/collector.json"],
                          env=e, capture_output=True, text=True)


def test_snapshot_is_committed_as_the_agent_and_pushed(tmp_path):
    remote, space = _space(tmp_path)
    r = _run(space, _fake_collector(tmp_path))
    assert r.returncode == 0, r.stderr
    log = _git("log", "-1", "--format=%an|%s", "main", cwd=remote)
    assert log.startswith("Miles|metrics: daily distribution snapshot")
    files = _git("show", "--name-only", "--format=", "main", cwd=remote).splitlines()
    assert files == [f"1-tracks/comms/metrics/{__import__('datetime').date.today().isoformat()}.json"]


def test_other_changes_in_the_space_are_left_alone(tmp_path):
    remote, space = _space(tmp_path)
    r = _run(space, _fake_collector(tmp_path))
    assert r.returncode == 0, r.stderr
    status = _git("status", "--porcelain", cwd=space)
    assert "unrelated.txt" in status  # still untracked, not committed by the job


def test_a_failed_collector_commits_nothing(tmp_path):
    remote, space = _space(tmp_path)
    broken = tmp_path / "broken.py"
    broken.write_text("import sys; sys.exit(3)\n")
    before = _git("rev-parse", "main", cwd=remote)
    r = _run(space, broken)
    assert r.returncode != 0
    assert _git("rev-parse", "main", cwd=remote) == before
