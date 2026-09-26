"""MEM-65: No one changes a machine's code, branch or services while a scheduled run is
using them.

Kind: deterministic. The machine's own code-changing path -- git_fleet_sync.sync_repo with
--execute --pull, which the fleet-sync timer and morning_repair.pull_latest (02:00 UTC)
both run -- against a tmp checkout whose origin (a local bare repo, so no GitHub gate is
consulted) has a new commit. A scheduled run is in progress: the test holds the run lock
nightshift's run.py itself holds for the whole overnight run
(~/.datacore/state/nightshift-run.lock under a tmp HOME, flock LOCK_EX).
  * while that run holds the lock, the pull must not move the checkout;
  * control: with no run in progress the same pull does move it.

Seeded failure: none needed to show red today (no updater consults the run lock); the
control proves the pull really changes code when allowed -- verified.
"""
import fcntl
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def _git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30,
                       env={**os.environ, **GIT_ENV})
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _setup(tmp: Path):
    origin, work, other = tmp / "origin.git", tmp / "data" / "2-space", tmp / "other"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, timeout=30)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, timeout=30,
                   env={**os.environ, **GIT_ENV})
    _git(work, "checkout", "-q", "-b", "main")
    (work / "run.py").write_text("print('v1')\n")
    _git(work, "add", "run.py")
    _git(work, "commit", "-q", "-m", "v1")
    _git(work, "push", "-q", "-u", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, timeout=30,
                   env={**os.environ, **GIT_ENV})
    (other / "run.py").write_text("print('v2')\n")
    _git(other, "commit", "-q", "-am", "v2")
    _git(other, "push", "-q", "origin", "main")
    home = tmp / "home"
    (home / ".datacore" / "state").mkdir(parents=True)
    return work, home


def _pull(work: Path, home: Path):
    code = ("import sys, json; sys.path.insert(0, %r); import git_fleet_sync as g; "
            "print(json.dumps(g.sync_repo(__import__('pathlib').Path(%r), execute=True, pull=True)))"
            % (str(LIB), str(work)))
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                          env={**os.environ, **GIT_ENV, "HOME": str(home)})


def test_code_is_not_changed_under_a_running_scheduled_run(tmp_path):
    work, home = _setup(tmp_path)
    before = _git(work, "rev-parse", "HEAD")
    with open(home / ".datacore" / "state" / "nightshift-run.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = _pull(work, home)
        after = _git(work, "rev-parse", "HEAD")
        body = (work / "run.py").read_text()
    assert after == before and "v1" in body, (
        "the checkout was pulled to new code while the overnight run held its run lock "
        f"(run.py now {body.strip()!r}); sync_repo said: {r.stdout.strip()[-300:]}{r.stderr[-300:]}")


def test_control_without_a_run_the_pull_changes_code(tmp_path):
    work, home = _setup(tmp_path)
    r = _pull(work, home)
    assert "v2" in (work / "run.py").read_text(), f"control pull did not move the checkout: {r.stdout}{r.stderr}"
