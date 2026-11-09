"""TSK-1: Every task is in exactly one of seven states (to do, next, waiting,
review, done, deferred, cancelled). Anything else is flagged.

Kind: deterministic (lint + adapter against a tmp space) plus one production
contract (the live org files lint clean, read-only).

Seeded failure: a live org file carrying a retired keyword (`* WORKING x`) or a
header that declares an eighth state (`BLOCKED`); the adapter asked to put a
task into BLOCKED. Each must be flagged / refused, and the file left as it was.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
ROOT = LIB.parent.parent
sys.path.insert(0, str(LIB))

import org_state_lint  # noqa: E402

ADAPTER = LIB / "org_workspace_adapter.py"
SEVEN = ("TODO", "NEXT", "WAITING", "REVIEW", "DONE", "DEFERRED", "CANCELLED")


def _space(root: Path, header: str = org_state_lint.CANON, body: str = "") -> Path:
    org = root / "5-evals" / "org"
    org.mkdir(parents=True)
    f = org / "next_actions.org"
    f.write_text(f"{header}\n{body}", encoding="utf-8")
    return f


def test_the_canon_is_exactly_the_seven_states():
    words = org_state_lint.CANON.replace("#+SEQ_TODO:", "").replace("|", " ").split()
    assert tuple(w.split("(")[0] for w in words) == SEVEN


def test_a_clean_file_passes(tmp_path, capsys):
    _space(tmp_path, body="".join(f"* {s} task {s.lower()}\n" for s in SEVEN))
    assert org_state_lint.lint(tmp_path) == 0


@pytest.mark.parametrize("state", ["WORKING", "QUEUED", "FAILED", "COMPLETED", "ACTIVE"])
def test_a_retired_state_is_flagged(tmp_path, capsys, state):
    _space(tmp_path, body=f"* {state} something\n")
    assert org_state_lint.lint(tmp_path) == 1
    assert f"retired keyword {state}" in capsys.readouterr().out


def test_an_eighth_declared_state_is_flagged(tmp_path, capsys):
    hdr = org_state_lint.CANON.replace("REVIEW(r!)", "REVIEW(r!) BLOCKED(b!)")
    _space(tmp_path, header=hdr, body="* BLOCKED stuck\n")
    assert org_state_lint.lint(tmp_path) == 1
    assert "non-canonical #+SEQ_TODO" in capsys.readouterr().out


def test_a_file_without_a_state_header_is_flagged(tmp_path, capsys):
    f = tmp_path / "5-evals" / "org"
    f.mkdir(parents=True)
    (f / "inbox.org").write_text("* TODO x\n", encoding="utf-8")
    assert org_state_lint.lint(tmp_path) == 1


def test_the_task_tool_refuses_a_state_outside_the_seven(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    f = _space(tmp_path)
    run = lambda *a: subprocess.run([sys.executable, str(ADAPTER), *a],  # noqa: E731
                                    capture_output=True, text=True)
    tid = json.loads(run("add", "--file", str(f), "--allow-any-file",
                         "--heading", "hello").stdout)["id"]
    before = f.read_text()
    r = run("update", "--file", str(f), "--id", tid, "--state", "BLOCKED")
    assert r.returncode != 0 and "error" in json.loads(r.stdout)
    assert f.read_text() == before
    assert "BLOCKED" not in f.read_text()


@pytest.mark.production
def test_live_org_files_hold_only_the_seven_states(capsys):
    rc = org_state_lint.lint(ROOT)
    out = capsys.readouterr().out
    assert rc == 0, [l for l in out.splitlines() if l.startswith("VIOLATION")][:10]
