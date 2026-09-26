"""SYN-2: When two machines change the same thing, Datacore merges it or shows
me the conflict; nothing is ever silently dropped or duplicated.

Kind: deterministic. Two machines (tmp clones A and B of one knowledge space,
a local bare origin), each syncing with the real `ledger_transport.converge`.

Promise, as evals:
  * same file, different lines: merged, both edits present exactly once;
  * same line edited on both: B's converge reports a conflict (not ok, reason
    says so), B's edit is kept in B's history, A's edit is on origin, and B's
    tree is left with no conflict markers and no half-finished merge;
  * both machines add a task at the end of the same org file: both tasks
    survive (merged or conflict shown), neither is lost, none is doubled;
  * both machines record the same task in the ledger (two writers, one id):
    after sync both events are held and the fold has ONE item, not two.

Seeded failure: a converge that resolves a conflict by taking one side
(`git merge -X theirs`), which silently drops B's edit.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import ledger_transport as lt
from ledger.fold import fold
from ledger.log import EventLog, read_events


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


NOTE = "".join(f"line {i}\n" for i in range(1, 11))
ORG = "#+TITLE: inbox\n* TODO existing task\n"


@pytest.fixture
def pair(tmp_path, monkeypatch):
    import actor_identity
    reg = tmp_path / "principals.yaml"
    reg.write_text("principals:\n  pa:\n    kind: agent\n    writes_as: [a]\n"
                   "  pb:\n    kind: agent\n    writes_as: [b]\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", reg)
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    monkeypatch.setattr(lt, "classify",
                        lambda space, root=None: lt.Result(True, "knowledge", {"entry": {"category": "knowledge"}}))
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True, timeout=60)
    clones = {}
    for name in ("seed", "a", "b"):
        path = tmp_path / name / "9-fixture"
        path.parent.mkdir()
        if name == "seed":
            subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, timeout=60)
            _git(path, "remote", "add", "origin", str(origin))
        else:
            subprocess.run(["git", "clone", "-q", str(origin), str(path)], check=True, timeout=60)
        for k, v in (("user.email", f"{name}@t"), ("user.name", name), ("core.hooksPath", str(hooks))):
            _git(path, "config", k, v)
        if name == "seed":
            (path / "org").mkdir()
            (path / "note.md").write_text(NOTE)
            (path / "org" / "inbox.org").write_text(ORG)
            _git(path, "add", "-A")
            _git(path, "commit", "-qm", "seed")
            assert _git(path, "push", "-q", "origin", "main").returncode == 0
        clones[name] = path
    return clones["a"], clones["b"], monkeypatch


def _sync(space: Path, monkeypatch, actor: str) -> lt.Result:
    monkeypatch.setenv("DATACORE_ACTOR", actor)
    return lt.converge(space)


def test_edits_to_different_lines_are_merged_once(pair):
    a, b, mp = pair
    (a / "note.md").write_text(NOTE.replace("line 2\n", "line 2 edited on A\n"))
    (b / "note.md").write_text(NOTE.replace("line 9\n", "line 9 edited on B\n"))
    assert _sync(a, mp, "a").ok
    assert _sync(b, mp, "b").ok
    for side in (b,):
        text = (side / "note.md").read_text()
        assert text.count("line 2 edited on A\n") == 1 and text.count("line 9 edited on B\n") == 1, text
        assert len(text.splitlines()) == 10, "a line was dropped or duplicated"


def test_the_same_line_edited_on_both_is_shown_not_dropped(pair):
    a, b, mp = pair
    (a / "note.md").write_text(NOTE.replace("line 5\n", "line 5 says A\n"))
    (b / "note.md").write_text(NOTE.replace("line 5\n", "line 5 says B\n"))
    assert _sync(a, mp, "a").ok
    res = _sync(b, mp, "b")
    assert not res.ok, f"a real conflict was reported as {res.reason!r}"
    assert "conflict" in res.reason, f"the conflict is not named: {res.reason!r}"
    assert "line 5 says B" in _git(b, "show", "HEAD:note.md").stdout, "B's edit was not kept"
    assert "line 5 says A" in _git(b, "show", "origin/main:note.md").stdout
    text = (b / "note.md").read_text()
    assert "<<<<<<<" not in text and "=======" not in text, "conflict markers left in the tree"
    assert not (b / ".git" / "MERGE_HEAD").exists(), "a half-finished merge was left behind"


def test_two_tasks_added_to_one_org_file_both_survive(pair):
    a, b, mp = pair
    (a / "org" / "inbox.org").write_text(ORG + "* TODO captured on A\n")
    (b / "org" / "inbox.org").write_text(ORG + "* TODO captured on B\n")
    ra = _sync(a, mp, "a")
    rb = _sync(b, mp, "b")
    assert ra.ok
    held_b = _git(b, "show", "HEAD:org/inbox.org").stdout
    if rb.ok:
        text = (b / "org" / "inbox.org").read_text()
        assert text.count("captured on A") == 1 and text.count("captured on B") == 1, text
    else:
        assert "conflict" in rb.reason, rb.reason
        assert "captured on B" in held_b, "B's task was lost"
    assert "captured on A" in _git(b, "show", "origin/main:org/inbox.org").stdout


def test_the_same_task_recorded_on_two_machines_is_one_item(pair):
    a, b, mp = pair
    for side, actor in ((a, "a"), (b, "b")):
        EventLog(side, actor, sign=False).append("item.create", {"id": "same", "title": "one task", "state": "NEXT"})
    assert _sync(a, mp, "a").ok
    assert _sync(b, mp, "b").ok
    for side in (b,):
        creates = [e for e in read_events(side) if e.type == "item.create" and e.payload.get("id") == "same"]
        assert {e.actor for e in creates} == {"a", "b"}, "an event was dropped in sync"
        items = [i for i in fold(read_events(side)).items if i == "same"]
        assert len(items) == 1
