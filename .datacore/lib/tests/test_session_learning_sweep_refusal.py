"""The session-learning sweep never counts a refused model as a swept day (OPS-13).

`claude -p` under an expired login or a usage limit prints its refusal on stdout
and exits non-zero. The sweep used to treat any stdout as success when the exit
code was non-zero, so a refusal was marked `done` and those sessions were never
learned from.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import session_learning_sweep as sweep  # noqa: E402


def _fake_run(rc: int, stdout: str, stderr: str = ""):
    def run(*a, **k):
        return subprocess.CompletedProcess(a[0] if a else [], rc, stdout, stderr)
    return run


@pytest.mark.parametrize("refusal", ["Invalid API key · Please run /login",
                                     "Claude AI usage limit reached|1790819914"])
def test_a_refusal_on_stdout_with_a_nonzero_exit_is_a_failure(monkeypatch, refusal):
    monkeypatch.setattr(sweep.subprocess, "run", _fake_run(1, refusal + "\n"))
    ok, out = sweep.run_claude("prompt")
    assert not ok
    assert refusal.split("|")[0] in out


def test_a_zero_exit_with_output_is_a_success(monkeypatch):
    monkeypatch.setattr(sweep.subprocess, "run", _fake_run(0, "swept 3 sessions\n"))
    assert sweep.run_claude("prompt") == (True, "swept 3 sessions")
