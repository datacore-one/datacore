"""The execution envelope's contract — bug classes 2 and 3, as tests.

Each case here is a real failure that reached production and was invisible to
the convention it replaced. They are written as behaviour, not implementation,
so the envelope can be rewritten without rewriting them.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import time

import pytest
import yaml

RUN = pathlib.Path(__file__).resolve().parents[1] / "jobs" / "run.py"


@pytest.fixture
def sandbox():
    """A throwaway DATACORE_ROOT with its own manifest."""
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / ".datacore" / "lib" / "jobs").mkdir(parents=True)
        yield root


def _write(root: pathlib.Path, jobs: list[dict]) -> None:
    (root / ".datacore" / "lib" / "jobs" / "manifest.yaml").write_text(
        yaml.safe_dump({"version": 1, "jobs": jobs}))


def _job(name: str, cmd: str, artifact: pathlib.Path, **extra) -> dict:
    j = {"name": name, "machine": "mac", "schedule": "* * * * *", "cmd": cmd,
         "on_fail": "log",
         "artifacts": [{"path": str(artifact), "check": "nonempty",
                        "max_age_hours": 1}]}
    j.update(extra)
    return j


def _run(root: pathlib.Path, name: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(RUN), name],
                          capture_output=True, text=True,
                          env=dict(os.environ, DATACORE_ROOT=str(root)))


# --- class 3: failures that do not fail ------------------------------------

def test_exit_zero_with_no_output_is_a_failure(sandbox):
    """`nlm audio download` prints a URL, exits, and writes nothing.

    Under the old convention that was a success. A job may not claim success
    without evidence it produced something.
    """
    art = sandbox / "out.log"
    _write(sandbox, [_job("noop", "true", art)])
    r = _run(sandbox, "noop")
    assert r.returncode == 1, r.stdout
    assert "POST-CONDITION FAILED" in r.stdout


def test_pipe_does_not_mask_the_real_exit_status(sandbox):
    """`cmd | tail` returns tail's status. Engram ENG-2026-08-19-018.

    It recurs because nothing enforces pipefail — including in a merge command
    written during this very session, which reported MERGE_EXIT=0 for a merge
    that had not run.
    """
    art = sandbox / "out.log"
    _write(sandbox, [_job("masked", "false | tail -1", art)])
    assert _run(sandbox, "masked").returncode == 2


def test_command_failure_is_distinct_from_contract_failure(sandbox):
    """Exit 2 (the command broke) must not be confused with exit 1 (it ran and
    produced nothing acceptable). Nightshift's "0 completed, 0 failed,
    0 skipped" was unreadable precisely because those collapsed together."""
    art = sandbox / "out.log"
    _write(sandbox, [_job("boom", "echo bad >&2; exit 7", art)])
    assert _run(sandbox, "boom").returncode == 2


def test_nothing_to_do_succeeds_when_the_artifact_is_fresh(sandbox):
    """A job with genuinely nothing to do must not be reported as broken.

    This is the distinction the contract exists to express, and getting it
    wrong in the other direction would just make a new class of false alarm.
    """
    art = sandbox / "out.log"
    art.write_text("from an earlier run\n")
    _write(sandbox, [_job("idle", "true", art)])
    assert _run(sandbox, "idle").returncode == 0


def test_nothing_to_do_fails_when_the_artifact_is_stale(sandbox):
    """Same job, same no-op — but now the evidence is old. That is the
    ledger-verify shape: a stale artifact that keeps passing a content check
    while nothing has written it for days."""
    art = sandbox / "out.log"
    art.write_text("stale\n")
    old = time.time() - 10 * 3600
    os.utime(art, (old, old))
    _write(sandbox, [_job("idle", "true", art)])
    assert _run(sandbox, "idle").returncode == 1


def test_honest_success_passes(sandbox):
    art = sandbox / "out.log"
    _write(sandbox, [_job("good", f"echo real > {art}", art)])
    assert _run(sandbox, "good").returncode == 0


# --- class 2: interactive env != scheduled env -----------------------------

def test_missing_required_env_prevents_the_run_entirely(sandbox):
    """OLLAMA_MODEL was unset in cron and logged `<unset>` for weeks.

    A precondition failure is exit 3 and the command never executes — running
    a job known to be misconfigured produces a confusing artifact rather than
    a clear error.
    """
    art = sandbox / "out.log"
    _write(sandbox, [_job("needs", f"echo ran > {art}", art,
                          required_env=["DEFINITELY_NOT_SET_XYZ"])])
    r = _run(sandbox, "needs")
    assert r.returncode == 3
    assert not art.exists(), "the command must not run when a precondition fails"


def test_path_is_normalized_regardless_of_the_calling_shell(sandbox):
    """The nlm bug: ~/go/bin is on an interactive PATH, not on launchd's.

    The envelope does not TEST for the difference, it removes it — the job
    gets the same PATH whether cron, launchd or a human started it. Here the
    caller's PATH is deliberately hostile and the job still sees the standard
    one.
    """
    art = sandbox / "out.log"
    _write(sandbox, [_job("pathcheck", f"echo $PATH > {art}", art)])
    r = subprocess.run(
        [sys.executable, str(RUN), "pathcheck"], capture_output=True, text=True,
        env=dict(os.environ, DATACORE_ROOT=str(sandbox), PATH="/nonexistent"))
    assert r.returncode == 0, r.stdout + r.stderr
    seen = art.read_text().strip()
    assert "/nonexistent" not in seen
    assert str(pathlib.Path.home() / "go" / "bin") in seen, seen


def test_undeclared_shell_variables_do_not_leak_in(sandbox):
    """A job must not silently depend on something that happens to be exported
    in a developer's shell but not in cron's — that is the whole class."""
    art = sandbox / "out.log"
    _write(sandbox, [_job("leak", f"echo \"[${{MY_LOCAL_HACK:-unset}}]\" > {art}", art)])
    r = subprocess.run(
        [sys.executable, str(RUN), "leak"], capture_output=True, text=True,
        env=dict(os.environ, DATACORE_ROOT=str(sandbox), MY_LOCAL_HACK="leaked"))
    assert r.returncode == 0, r.stdout
    assert art.read_text().strip() == "[unset]"


def test_unknown_job_is_reported_as_undeclared(sandbox):
    """Bug class 4 from the other direction: you cannot run what you did not
    declare, so it cannot exist unverified."""
    _write(sandbox, [])
    assert _run(sandbox, "nope").returncode == 4
