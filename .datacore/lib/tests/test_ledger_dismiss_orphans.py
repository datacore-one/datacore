"""Tests for ledger_dismiss_orphans — closing items org no longer has.

This tool acts on ABSENCE, which is weak evidence: a truncated file or a bad
parse looks identical to a deleted task, and dismiss is terminal (DIP-0034)
with no undo. The guards are the point, so they are what these test.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import ledger_dismiss_orphans as orph  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402


#: Enough present items that ONE orphan sits under the 10% ceiling. A
#: two-item fixture put the single orphan at 50% and the guard correctly
#: refused the sweep — the fixture was unrealistic, not the ceiling.
PRESENT = [f"task-present-{n}" for n in range(12)]


@pytest.fixture
def space(tmp_path):
    space = tmp_path / "0-testspace"
    (space / "org").mkdir(parents=True)
    (space / "org" / "next_actions.org").write_text(
        "".join(f"* NEXT Still here {n}\n:PROPERTIES:\n:ID: {n}\n:END:\n"
                for n in PRESENT),
        encoding="utf-8")
    log = EventLog(space, "test")
    for nid in [*PRESENT, "task-gone"]:
        log.append("item.create", {"id": nid, "title": nid, "state": "NEXT"})
    return space


def _status(space, nid):
    return fold(read_events(space)).items[nid].status


def test_finds_only_the_item_org_no_longer_has(space):
    assert orph.orphans(space)["orphans"] == ["task-gone"]


def test_dry_run_writes_nothing(space, monkeypatch, capsys):
    before = len(read_events(space))
    monkeypatch.setattr(sys, "argv", ["x", "--root", str(space.parent)])
    orph.main()
    assert len(read_events(space)) == before
    assert _status(space, "task-gone") in orph.LIVE


def test_execute_dismisses_as_housekeeping(space, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["x", "--root", str(space.parent), "--execute"])
    orph.main()

    from ledger.fold import closure_kind
    items = fold(read_events(space)).items
    assert items["task-gone"].status == "dismissed"
    assert closure_kind(items["task-gone"]) == "housekeeping"
    assert all(items[n].status in orph.LIVE for n in PRESENT)


def test_a_task_moved_to_another_org_file_is_not_an_orphan(space):
    """Whole-space scan: merely moving a task must never look like deleting it."""
    (space / "1-active").mkdir()
    (space / "1-active" / "project.org").write_text(
        "* NEXT Moved here\n:PROPERTIES:\n:ID: task-gone\n:END:\n", encoding="utf-8")

    assert orph.orphans(space)["orphans"] == []


def test_archived_task_is_not_an_orphan(space):
    (space / "org" / "next_actions_archive.org").write_text(
        "* DONE Archived\n:PROPERTIES:\n:ID: task-gone\n:END:\n", encoding="utf-8")

    assert orph.orphans(space)["orphans"] == []


def test_checkpoint_projection_is_not_evidence(space):
    """A checkpoint is a rendering OF the ledger, not of org.

    Counting it made every live item look present, so the first real run
    found zero orphans while shadow_check was reporting fourteen.
    """
    ckpt = space / ".datacore" / "checkpoints"
    ckpt.mkdir(parents=True)
    (ckpt / "next_actions.org").write_text(
        "* NEXT Gone\n:PROPERTIES:\n:ID: task-gone\n:END:\n", encoding="utf-8")

    assert orph.orphans(space)["orphans"] == ["task-gone"]


def test_directory_named_dot_org_does_not_skip_the_space(space):
    """`*.org` matches directories too — 3-fds vendors `vendor/golang.org/`."""
    (space / "vendor" / "golang.org").mkdir(parents=True)

    r = orph.orphans(space)

    assert not r.get("skipped")
    assert r["orphans"] == ["task-gone"]


def test_ceiling_refuses_an_implausible_sweep(space, monkeypatch, capsys):
    """A scan that finds most of the corpus missing is broken, not tidy."""
    log = EventLog(space, "test")
    for n in range(20):
        log.append("item.create", {"id": f"bulk-{n}", "title": "x", "state": "NEXT"})

    monkeypatch.setattr(sys, "argv", ["x", "--root", str(space.parent), "--execute"])
    orph.main()

    assert "REFUSED" in capsys.readouterr().out
    assert _status(space, "task-gone") in orph.LIVE, "nothing may be dismissed"


def test_unreadable_org_file_skips_the_whole_space(space, monkeypatch):
    """Not being able to read a file is not evidence a task is gone."""
    bad = space / "org" / "broken.org"
    bad.write_text("x", encoding="utf-8")

    real = Path.read_text

    def boom(self, *args, **kwargs):
        if self.name == "broken.org":
            raise OSError("permission denied")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)
    r = orph.orphans(space)

    assert r["skipped"] == "unreadable org file(s)"
    assert r["orphans"] == []
