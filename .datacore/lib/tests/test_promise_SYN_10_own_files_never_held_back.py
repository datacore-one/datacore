"""SYN-10: A file Datacore writes itself never gets stuck behind Datacore's own sync
rules; if it would, the job that wrote it fails at that moment and says why.

Evidence (2026-10-28 fleet-logs bug-classes report, class 11): the ledger checkpoint
is kept tracked in git on purpose (ledger_checkpoint.py module docstring, "Deliberately
NOT gitignored ... a restore point that dies with the machine is not one"), while the
transport refuses to share any file of MAX_SHARED_BYTES or more
(ledger_transport.py:806, 50 MB; `unshareable()` at :809). 1-datafund's checkpoint
reached about 100 MB, so every day the sync reported it "held back, still only on this
machine" -- and `ledger_checkpoint.write` had returned success. The writer never learns
that what it wrote cannot leave the machine; the restore point it promises does not
exist off this machine.

Kind: deterministic, through the real code. A throwaway space (tmp_path, its own git
repository, no remote) gets a small real event log; the share cap is monkeypatched
small instead of writing 50 MB. Then the REAL `ledger_checkpoint.write` runs, and the
REAL `ledger_transport.unshareable` judges what it left in the tree after `git add -A`
-- exactly what the autosave stages.

Kept when, with a checkpoint larger than the share cap, either
  - `write` fails at write time and its error says the checkpoint is too large to
    share (size / cap / share named), or
  - nothing `write` put on disk is a path the transport would hold back (for example
    the checkpoint is no longer a tracked/shared path, or it is split under the cap).
Boundary: under the cap, `write` still succeeds and the checkpoint restores -- a fix
that refuses every checkpoint does not keep the promise.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

import ledger_checkpoint as checkpoint
import ledger_transport as transport


@pytest.fixture(autouse=True)
def declared_backup_writer(monkeypatch):
    monkeypatch.setenv("DATACORE_ACTOR", "checkpoint-test")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=True).stdout


def _space(tmp_path: Path) -> Path:
    from ledger.log import EventLog
    space = tmp_path / "1-datafund"
    log = EventLog(space, "writer")
    for i in range(6):
        log.append("item.create", {"id": f"t{i}", "title": f"Keep restore point {i}",
                                   "state": "TODO", "level": 1,
                                   "org": {"body": "x" * 200}})
    _git(space, "init", "-q", "-b", "main")
    _git(space, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "add", "-A")
    _git(space, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "commit", "-q", "-m", "events")
    return space


def _written(space: Path) -> list[str]:
    org, snapshot = checkpoint.checkpoint_paths(space)
    return [str(p.relative_to(space)) for p in (org, snapshot) if p.exists()]


def test_checkpoint_over_the_share_cap_fails_at_write_or_is_never_held_back(tmp_path, monkeypatch):
    space = _space(tmp_path)
    # Small cap: the checkpoint this space produces is several KB.
    cap = 1024
    monkeypatch.setattr(transport, "MAX_SHARED_BYTES", cap)

    try:
        checkpoint.write(space)
    except Exception as exc:  # the promise allows failing at write time -- if it says why
        said = str(exc).lower()
        assert re.search(r"too large|share|cap|size|mb|bytes", said), (
            f"checkpoint write failed, but not because the result is too large to share: {exc!r}")
        return

    # write() succeeded: then what it wrote must not be held back by the sync.
    _git(space, "add", "-A")
    held = transport.unshareable(space)
    mine = {p: why for p, why in held.items() if p in set(_written(space))}
    assert not mine, (
        "ledger_checkpoint.write reported success, but the sync will hold back what it "
        f"wrote, so it is still only on this machine: {mine} (share cap {cap} bytes)")


def test_checkpoint_under_the_share_cap_is_written_and_restores(tmp_path, monkeypatch):
    space = _space(tmp_path)
    monkeypatch.setattr(transport, "MAX_SHARED_BYTES", 50 * 1024 * 1024)
    checkpoint.write(space)
    ok, detail = checkpoint.verify(space)
    assert ok, detail
    _git(space, "add", "-A")
    held = transport.unshareable(space)
    assert not [p for p in held if p in set(_written(space))], held
