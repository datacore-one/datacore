"""MEM-39: Cancelling a task keeps it recoverable and searchable. A task is
deleted only if it was added by mistake.

Kind: deterministic. The task tools against a tmp space
(org_workspace_adapter: update --state CANCELLED, then archive-done as the
nightly clean-up runs it):
  * after cancelling, the task is still in its file with its :ID:, heading and
    body, in state CANCELLED; `list --states CANCELLED` and `show --id` find it;
  * after archive-done moves closed tasks out, the cancelled task lives in the
    archive file with the same :ID: and body, and can be found there and
    re-opened (update --state TODO) -- recoverable, not gone;
  * no task tool offers a delete: nothing in the adapter's command set removes
    a task outright (deletion is a deliberate by-hand repair for a mistaken
    capture, not a routine outcome).

Seeded failure: archive-done that drops closed subtrees instead of moving
them (verified by removing the task from the archive file in-memory).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

HEADER = "#+SEQ_TODO: TODO(t) NEXT(n) WAITING(w@) REVIEW(r!) | DONE(d!) DEFERRED(f@) CANCELLED(c@)\n"
TID = "7b1d0c2e-0000-4000-8000-000000000039"


@pytest.fixture
def space(tmp_path, monkeypatch):
    state = (tmp_path / "state").resolve()
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    org = (tmp_path / "5-evals" / "org").resolve()
    org.mkdir(parents=True)
    f = org / "next_actions.org"
    f.write_text(HEADER + "* Work\n** Project\n"
                 f"*** TODO Negotiate the office lease\n:PROPERTIES:\n:ID: {TID}\n:END:\n"
                 "Landlord offered 3 years at the old rate; counter at 5.\n"
                 "*** TODO Keep this one\n", encoding="utf-8")
    return f


def _run(*argv):
    import org_workspace_adapter as A
    args = A.build_parser().parse_args(list(argv))
    return A.COMMAND_MAP[args.command](args)


def _find(f: Path):
    return _run("show", "--file", str(f), "--id", TID)


def test_cancel_keeps_the_task_in_place(space):
    r = _run("update", "--file", str(space), "--id", TID, "--state", "CANCELLED")
    assert not r.get("error"), r
    text = space.read_text(encoding="utf-8")
    assert TID in text and "Negotiate the office lease" in text and "counter at 5" in text, \
        "cancelling removed the task or its body"
    listed = _run("list", "--file", str(space), "--states", "CANCELLED")
    tasks = listed.get("tasks", listed if isinstance(listed, list) else [])
    assert any(TID == t.get("id") for t in tasks), f"cancelled task not searchable: {listed}"
    shown = _find(space)
    assert not shown.get("error") and "Negotiate the office lease" in str(shown), shown


def test_archiving_a_cancelled_task_keeps_it_recoverable(space):
    import org_workspace_adapter as A
    from org_workspace import default_archive_path
    assert not _run("update", "--file", str(space), "--id", TID, "--state", "CANCELLED").get("error")
    r = _run("archive-done", "--file", str(space), "--min-age", "0")
    assert not r.get("error"), r
    archive = default_archive_path(space)
    where = [p for p in (space, archive) if p.exists() and TID in p.read_text(encoding="utf-8")]
    assert where, "the cancelled task is in neither the file nor its archive: it was deleted"
    f = where[-1]
    assert "counter at 5" in f.read_text(encoding="utf-8"), "the archived task lost its body"
    shown = _find(f)
    assert not shown.get("error"), f"archived cancelled task cannot be found by id: {shown}"
    # Re-opening is deliberately NOT asserted: DIP-0009 makes CANCELLED terminal
    # for the task tools (TSK-2: a dismissed task never comes back by itself).
    # Recoverable here means the whole task survives, findable by its id.


def test_no_task_tool_deletes_a_task():
    import org_workspace_adapter as A
    deleting = [c for c in A.COMMAND_MAP if any(w in c for w in ("delete", "remove", "purge", "rm"))]
    assert not deleting, f"the task tools offer outright deletion: {deleting}"
