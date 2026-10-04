"""ledger_install (profile A): a shared space's commits pass the ledger write gate.

Under owner decision 7 (2026-10-04) signing waits for Phase 6, and the write-side
gate (LED-3: only EventLog-written lines, refused at commit and push) is what keeps
hand-written events out. It runs only when the checkout's hooks are Datacore's
(`.datacore/githooks`), and nothing set that up for a team's space.

  * init on a space that is a git checkout wires the hooks when it has none,
    and leaves a checkout that already has its own hooks alone;
  * doctor names a git space whose commits skip the gate, with the fix;
  * a space that is not a git checkout (one host, no sharing) is not a gap.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import ledger_install as inst

HOOKS_KEY = "core." + "hooksPath"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    code = tmp_path / "Data"
    gh = code / ".datacore" / "githooks"
    gh.mkdir(parents=True)
    (gh / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
    (gh / "pre-commit").chmod(0o755)
    monkeypatch.setattr(inst, "GITHOOKS", gh)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DATACORE_ROOT", str(code))
    monkeypatch.setenv("DATACORE_IDENTITY_FILE", str(tmp_path / "home" / ".datacore" / "identity.env"))
    monkeypatch.delenv("DATACORE_ACTOR", raising=False)
    monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)
    return code


def _repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, timeout=60)
    return path


def test_init_wires_the_write_gate_into_a_git_space(machine):
    space = _repo(machine / "team")
    out = inst.prepare_space(space)
    assert _git(space, "config", HOOKS_KEY).stdout.strip() == str(inst.GITHOOKS), \
        f"init left the space's commits outside the ledger write gate: {out}"
    assert any("write gate" in line for line in out), f"init did not say it wired the gate: {out}"


def test_init_leaves_a_checkout_with_its_own_hooks_alone(machine, tmp_path):
    space = _repo(machine / "team")
    own = tmp_path / "own-hooks"
    own.mkdir()
    (own / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
    (own / "pre-commit").chmod(0o755)
    _git(space, "config", HOOKS_KEY, str(own))
    inst.prepare_space(space)
    assert _git(space, "config", HOOKS_KEY).stdout.strip() == str(own), "init replaced the checkout's own hooks"


def test_doctor_names_a_git_space_that_skips_the_gate(machine):
    space = _repo(machine / "team")
    (space / ".datacore" / "events").mkdir(parents=True)
    (space / ".datacore" / "ledger-edit-protocol").write_text("2\n")
    gaps = [c for c in inst.doctor(space) if c.ok is False and "write gate" in c.name]
    assert gaps and HOOKS_KEY in gaps[0].fix, f"doctor did not name the missing write gate with its fix: {gaps}"

    plain = machine / "solo"
    (plain / ".datacore" / "events").mkdir(parents=True)
    (plain / ".datacore" / "ledger-edit-protocol").write_text("2\n")
    assert not [c for c in inst.doctor(plain) if c.ok is False and "write gate" in c.name], \
        "a space that is not a git checkout was reported as missing the write gate"



def test_a_first_verify_leaves_the_runtime_state_private(tmp_path, monkeypatch):
    """On a fresh machine the first `verify` created ~/.datacore/state as 0755 (the
    verified-marker cache), and every later converge then failed: "runtime state
    directory must be private to its identity" (profile A runbook rehearsal,
    2026-10-04). The state folder stays private to its user."""
    import file_utils
    from ledger.log import EventLog
    from ledger.verify import verify_log
    state = tmp_path / "home" / ".datacore" / "state"
    monkeypatch.setenv("DATACORE_STATE", str(state))
    monkeypatch.setenv("DATACORE_ROOT", str(tmp_path / "Data"))
    space = tmp_path / "Data" / "team"
    EventLog(space, "alice", sign=False).append("item.create", {"id": "t-1", "title": "x"})
    verify_log(space / ".datacore" / "events" / "alice.jsonl", incremental=True)
    assert state.exists(), "the verified marker was not written; the case is not exercised"
    assert not state.stat().st_mode & 0o077, f"verify left the state folder open: {oct(state.stat().st_mode & 0o777)}"
    file_utils.private_state_directory("locks", data_root=tmp_path / "Data")
