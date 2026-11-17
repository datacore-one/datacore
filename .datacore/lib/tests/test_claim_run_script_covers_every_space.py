"""The scheduled claim run covers every space and leaves a status a contract can read.

Audit 2026-11-15, section 4.1 items 3, 4 and 8. `ledger_claim_run.sh` ran for
the system space only, while the delegation watcher reads every space; it never
ran the second check; and its only evidence was a log that was recent.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ledger.log import EventLog

LIB = Path(__file__).resolve().parents[1]
SCRIPT = LIB / "ledger_claim_run.sh"


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _install(tmp_path, spaces: dict):
    """A scratch installation: `spaces` maps a space name to its item payloads."""
    root = tmp_path / "Data"
    (root / ".datacore").mkdir(parents=True)
    (root / ".datacore" / "lib").symlink_to(LIB)                # the script runs <root>/.datacore/lib
    for name, items in spaces.items():
        space = root / name
        (space / ".datacore" / "events").mkdir(parents=True)
        (space / ".datacore" / "config.yaml").write_text(f"space:\n  name: {name}\n  type: team\n")
        (space / ".gitignore").write_text(".datacore/state/*\n")
        for payload in items:
            EventLog(space, "delegator", sign=False).append("item.create", payload)
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "d@example.invalid"],
                     ["config", "user.name", "d"]):
            _git(space, *args)
        _git(space, "add", "-A")
        _git(space, "commit", "-qm", "seed")
    return root


def _run(tmp_path, root, *args):
    state = tmp_path / "state"
    state.mkdir(mode=0o700, exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    import sys
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}", "HOME": str(home), "DATACORE_STATE": str(state), "DATACORE_ROOT": str(root),
           "DATACORE_ACTOR": "tester", "DATACORE_LEDGER_SIGN": "0",
           # the scratch installation, not this checkout, is what the run may write
           "DATACORE_LEDGER_WRITE_FENCE": ""}
    r = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=300)
    status = state / "ledger-claim-status.txt"
    log = state / "ledger-claim.log"
    return r, (status.read_text() if status.exists() else ""), (log.read_text() if log.exists() else "")


@pytest.fixture(autouse=True)
def _real_lib_reads_the_scratch_root(monkeypatch):
    # The script runs this checkout's lib against the scratch installation.
    monkeypatch.delenv("DATACORE_IDENTITY_FILE", raising=False)


def test_with_no_space_given_every_ledger_space_gets_a_run_and_a_second_check(tmp_path):
    root = _install(tmp_path, {"1-alpha": [], "2-beta": []})
    (root / "3-notes").mkdir()                                  # no ledger: not a claim space
    r, status, log = _run(tmp_path, root)
    assert r.returncode == 0, r.stderr + log
    assert status.startswith("OK claim ") and status.rstrip().endswith("spaces=2"), status
    for name in ("1-alpha", "2-beta"):
        assert f"CLAIM-STATUS {name} OK" in log, log
    assert log.count("verified 0, recheck failed 0") == 2, "the second check ran in each space"
    assert "3-notes" not in log


def test_an_item_that_needs_a_person_turns_the_status_red_and_names_the_space(tmp_path):
    root = _install(tmp_path, {
        "1-alpha": [],
        "2-beta": [{"id": "x-1", "title": "send the email", "assignee": "tester", "effects": ["email.send"]}],
    })
    r, status, log = _run(tmp_path, root)
    assert r.returncode == 1
    assert status.startswith("FAIL claim "), status
    assert "2-beta: refused=1" in status and "1-alpha" not in status, status


def test_one_named_space_still_runs_alone(tmp_path):
    root = _install(tmp_path, {"1-alpha": [], "2-beta": []})
    r, status, log = _run(tmp_path, root, str(root / "2-beta"), "tester")
    assert r.returncode == 0, r.stderr + log
    assert status.rstrip().endswith("spaces=1") and "1-alpha" not in log


def test_a_damaged_ledger_line_is_a_fail_not_a_quiet_ok(tmp_path):
    """The reader skips a damaged line so one bad record never stops the space;
    what it hid may be delegated work, so the run must not report OK."""
    root = _install(tmp_path, {"1-alpha": []})
    (root / "1-alpha" / ".datacore" / "events" / "delegator.jsonl").write_text("not json at all\n")
    r, status, log = _run(tmp_path, root)
    assert r.returncode == 1
    assert status.startswith("FAIL claim ") and "1-alpha: a ledger log has a damaged line" in status, status + log
    assert "events/delegator.jsonl" in status


def test_a_missing_dispatcher_is_a_fail_that_says_so(tmp_path):
    root = _install(tmp_path, {"1-alpha": []})
    (root / ".datacore" / "lib").unlink()
    (root / ".datacore" / "lib").mkdir()                        # an installation with no code
    r, status, log = _run(tmp_path, root, str(root / "1-alpha"), "tester")
    assert r.returncode == 1
    assert status.startswith("FAIL claim ") and "printed no status" in status, status


def test_work_that_could_not_be_published_is_a_fail_that_names_the_space(tmp_path):
    """A host whose results never leave it is not working. On 2026-11-16 one
    agent's claim run had ended "converged but not published" on every tick for
    four days, and the job's contract stayed green."""
    root = _install(tmp_path, {"1-alpha": []})
    space = root / "1-alpha"
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    _git(space, "remote", "add", "origin", str(remote))
    _git(space, "push", "-q", "-u", "origin", "main")
    hook = remote / "hooks" / "pre-receive"                     # from now on the remote refuses
    hook.write_text("#!/bin/sh\necho 'refused by the remote' >&2\nexit 1\n")
    hook.chmod(0o755)
    (space / "note.md").write_text("a result\n")
    _git(space, "add", "note.md")
    _git(space, "commit", "-qm", "a result to publish")
    r, status, log = _run(tmp_path, root)
    assert r.returncode == 1, log
    assert status.startswith("FAIL claim ") and "1-alpha: not published" in status, status + log
