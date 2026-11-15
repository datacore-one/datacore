"""A claim run commits what the run changed -- and only that.

Audit 2026-11-15, section 4.1 items 2 and 7. A space is written to all day by
other jobs (the journal, captures, generated lists). The claim loop used to

  * STOP the whole run when anything in the space was uncommitted, so one stray
    file meant no delegated item ran at all, and
  * commit EVERY dirty path as the item's result, so another writer's change
    made in the meantime was swept into `result: <id>`.

Now the loop records what is already uncommitted before the agent runs, and
afterwards commits only what the run itself changed. The check still runs in a
fresh worktree of the committed result.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import ledger_claim
from ledger.log import EventLog
from ledger.policy import guarded_append


@pytest.fixture(autouse=True)
def _roster(tmp_path_factory, monkeypatch):
    p = tmp_path_factory.mktemp("reg") / "principals.yaml"
    p.write_text("principals:\n"
                 "  miles: {kind: agent, writes_as: [miles, nightshift]}\n"
                 "  winston: {kind: agent, writes_as: [winston, bridge]}\n")
    import actor_identity
    monkeypatch.setattr(actor_identity, "PRINCIPALS", p)


def _git(space, *args):
    return subprocess.run(["git", "-C", str(space), *args], check=True, capture_output=True, text=True).stdout


def _space(tmp_path, items):
    space = tmp_path / "space"
    space.mkdir(exist_ok=True)
    for iid, check in items:
        guarded_append(EventLog(space, "winston", sign=False), "item.create",
                       {"id": iid, "title": f"task {iid}", "assignee": "miles", "check": check})
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "d@example.invalid"],
                 ["config", "user.name", "d"]):
        _git(space, *args)
    (space / "notes.md").write_text("tracked\n")
    # What a real space ignores (delegation_drill.delegation_space): the
    # writer's own runtime state is not an artifact.
    (space / ".gitignore").write_text(".datacore/state/*\n")
    _git(space, "add", "-A")
    _git(space, "commit", "-qm", "seed")
    return space


def _dispatch(space, capsys, monkeypatch, agent):
    def run(title, route, cwd, item_id="", hops=0, actor=""):
        agent(Path(cwd), item_id)
        return True, "did it", {"executor": "fake", "model": "m", "cost_cents": 0, "duration_s": 0}
    monkeypatch.setattr(ledger_claim, "run_task", run)
    argv = sys.argv
    sys.argv = ["ledger_claim.py", "--space", str(space), "--actor", "miles", "--execute"]
    try:
        ledger_claim.main()              # non-zero when an item failed; the tests read the ledger
    finally:
        sys.argv = argv
    return capsys.readouterr().out


def _status(space, iid):
    from ledger.fold import fold
    from ledger.log import read_events
    return fold(list(read_events(space))).items[iid].status


def test_a_stray_uncommitted_file_neither_stops_the_run_nor_joins_the_result(tmp_path, capsys, monkeypatch):
    space = _space(tmp_path, [("item-1", "test -f answer.txt")])
    (space / "someone-elses-draft.md").write_text("another writer's work in progress\n")
    (space / "notes.md").write_text("tracked, and edited by someone else\n")

    out = _dispatch(space, capsys, monkeypatch, lambda cwd, iid: (cwd / "answer.txt").write_text("42\n"))

    assert "STOPPING" not in out, out
    assert _status(space, "item-1") == "completed", out
    result = _git(space, "log", "--format=%H", "--grep=^result: item-1$").split()
    assert len(result) == 1, "one result commit for the item"
    committed = _git(space, "show", "--name-only", "--format=", result[0]).split()
    assert committed == ["answer.txt"], committed
    left = _git(space, "status", "--porcelain").split()
    assert "someone-elses-draft.md" in left and "notes.md" in left, "the other writer's work is exactly as it was"


def test_two_items_each_commit_their_own_work(tmp_path, capsys, monkeypatch):
    space = _space(tmp_path, [("item-1", "test -f item-1.txt"), ("item-2", "test -f item-2.txt")])
    (space / "left-behind.txt").write_text("uncommitted\n")

    out = _dispatch(space, capsys, monkeypatch, lambda cwd, iid: (cwd / f"{iid}.txt").write_text("done\n"))

    assert _status(space, "item-1") == "completed" and _status(space, "item-2") == "completed", out
    assert "left-behind.txt" not in _git(space, "log", "--name-only", "--format=").split()


def test_an_agent_that_edits_another_writers_uncommitted_file_is_not_committed(tmp_path, capsys, monkeypatch):
    """The two changes cannot be told apart, so neither is committed: the item is
    released with the reason and the file stays as it is for a person."""
    space = _space(tmp_path, [("item-1", "true")])
    (space / "notes.md").write_text("tracked, and edited by someone else\n")

    def agent(cwd, iid):
        (cwd / "notes.md").write_text("tracked, edited by someone else, and then by the agent\n")
    out = _dispatch(space, capsys, monkeypatch, agent)

    assert _status(space, "item-1") != "completed", out
    assert "notes.md" in out and "another writer" in out, out
    assert not _git(space, "log", "--format=%H", "--grep=^result: item-1$").split(), "nothing committed as its result"
    assert "edited by someone else, and then by the agent" in (space / "notes.md").read_text(), "left for a person"


def test_the_snapshot_is_none_when_the_space_is_not_a_repository(tmp_path):
    assert ledger_claim._dirty_snapshot(tmp_path) is None


def test_a_check_ignores_what_was_already_uncommitted_but_not_what_the_run_left(tmp_path):
    space = _space(tmp_path, [])
    (space / "draft.md").write_text("pre-existing\n")
    baseline = ledger_claim._dirty_snapshot(space)
    assert list(baseline) == ["draft.md"]
    assert ledger_claim._artifact_tree_clean(space, baseline=baseline)
    (space / "agent-forgot-to-commit.txt").write_text("x\n")
    dirty: list[str] = []
    assert not ledger_claim._artifact_tree_clean(space, dirty, baseline=baseline)
    assert dirty == ["agent-forgot-to-commit.txt"]
