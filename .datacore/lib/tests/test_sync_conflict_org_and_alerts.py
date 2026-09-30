"""Two owner decisions of 2026-09-30 on sync conflicts (after SYN-9):

  * ".org files: merge by task ID, that's why we have org-workspace." A
    conflicting .org file is merged task by task (org_sync_merge): no duplicate
    task, no duplicate :ID:, the file parses. A person is asked only when both
    hosts changed the same field of the same task differently, or text without
    an :ID: had to be line-unioned.
  * "Alert once, then quiet." The sync that first meets a conflict reports it
    (not ok) and files the task. Later syncs, while that task is open, name the
    file but count as handled: not a failure, not a new alert, and never a
    success word (SYN-6). The box's cos_sync alerts once per conflict, even when
    another host met it first.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import ledger_transport as lt
from ledger.fold import fold
from ledger.log import read_events

LIB = Path(__file__).resolve().parents[1]
COS_SYNC = LIB.parent / "modules" / "chief-of-staff" / "server" / "lib" / "cos_sync.sh"
SPACE = "9-fixture"
SUCCESS = re.compile(r"\bclean\b|synced|PUSHED|converged\b(?! but)")   # SYN-6's words

ORG = """* Projects
** TODO Write the report
   :PROPERTIES:
   :ID: task-1
   :END:
** TODO Call the bank
   :PROPERTIES:
   :ID: task-2
   :END:
"""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """A registered knowledge space checked out on this host (b, under root) and
    on another host (a); a bare origin between them."""
    import actor_identity
    root = tmp_path / "Data"
    reg = root / ".datacore" / "registry"
    reg.mkdir(parents=True)
    (reg / "principals.yaml").write_text("principals:\n  p:\n    kind: agent\n    writes_as: [tester]\n"
                                         "  q:\n    kind: agent\n    writes_as: [other]\n")
    (reg / "repositories.yaml").write_text(f"repositories:\n  {SPACE}:\n    category: knowledge\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", reg / "principals.yaml")
    monkeypatch.setenv("DATACORE_ROOT", str(root))
    monkeypatch.setenv("DATACORE_ACTOR", "tester")
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    (tmp_path / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True, timeout=60)
    b = root / SPACE
    a = tmp_path / "other" / SPACE
    a.parent.mkdir()
    subprocess.run(["git", "clone", "-q", str(origin), str(b)], check=True, timeout=60)
    for k, v in (("user.email", "b@t"), ("user.name", "b"), ("core.hooksPath", str(hooks))):
        _git(b, "config", k, v)
    (b / "org").mkdir()
    (b / "org" / "next_actions.org").write_text(ORG)
    (b / "note.md").write_text("one\ntwo\nthree\n")
    (b / ".gitignore").write_text(".datacore/state/*\n")
    _git(b, "add", "-A")
    _git(b, "commit", "-qm", "seed")
    assert _git(b, "push", "-q", "-u", "origin", "HEAD:main").returncode == 0
    _git(b, "remote", "set-head", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(a)], check=True, timeout=60)
    for k, v in (("user.email", "a@t"), ("user.name", "a"), ("core.hooksPath", str(hooks))):
        _git(a, "config", k, v)
    return {"root": root, "a": a, "b": b, "origin": origin, "tmp": tmp_path}


def _both_edit(w: dict, rel: str, old: str, va: str, vb: str) -> None:
    """The other host publishes its edit first; this host commits its own."""
    for repo, new in ((w["a"], va), (w["b"], vb)):
        f = repo / rel
        text = f.read_text()
        assert old in text, (rel, old)
        f.write_text(text.replace(old, new, 1))
        _git(repo, "commit", "-qam", f"edit {rel}")
    assert _git(w["a"], "push", "-q", "origin", "main").returncode == 0


def _conflict_items(space: Path) -> list:
    return [i for i in fold(read_events(space)).items.values()
            if isinstance((i.payload or {}).get("sync_conflict"), dict)]


def _ids(path: Path) -> list[str]:
    from org_workspace import OrgWorkspace
    ws = OrgWorkspace()
    ws.load(path)                                   # refuses duplicate ids
    return [n.id() for n in ws.all_nodes() if n.id()]


# ─────────────────────────────── .org: merged by task id ───────────────────────────────

def test_org_same_task_edited_on_both_sides_merges_to_one_task_without_a_person(fleet):
    w = fleet
    _both_edit(w, "org/next_actions.org", "** TODO Write the report",
               "** TODO Write the report  :urgent:", "** NEXT Write the report")

    res = lt.converge(w["b"], root=w["root"])

    ids = _ids(w["b"] / "org" / "next_actions.org")
    assert ids.count("task-1") == 1 and ids.count("task-2") == 1, ids
    head = next(l for l in (w["b"] / "org" / "next_actions.org").read_text().splitlines()
                if "Write the report" in l)
    assert head.startswith("** NEXT") and ":urgent:" in head, head
    assert res.ok, res                              # merged by id: nothing waits for a person
    assert not _conflict_items(w["b"])
    shown = _git(w["origin"], "show", "main:org/next_actions.org").stdout
    assert shown.count(":ID: task-1") == 1, shown


def test_org_same_field_changed_differently_is_one_task_and_the_conflict_task_names_the_other(fleet):
    w = fleet
    _both_edit(w, "org/next_actions.org", "Write the report",
               "Write the annual report", "Write the Q3 report")

    res = lt.converge(w["b"], root=w["root"])

    path = w["b"] / "org" / "next_actions.org"
    assert _ids(path).count("task-1") == 1
    text = path.read_text()
    assert "Write the Q3 report" in text and "annual" not in text, text
    assert not res.ok and "org/next_actions.org" in res.reason, res
    (item,) = _conflict_items(w["b"])
    assert "annual" in item.payload.get("body", ""), item.payload.get("body")


def test_org_different_tasks_added_on_both_sides_are_each_present_once(fleet):
    w = fleet
    tail = "   :ID: task-2\n   :END:\n"
    _both_edit(w, "org/next_actions.org", tail,
               tail + "** TODO Added there\n   :PROPERTIES:\n   :ID: task-a\n   :END:\n",
               tail + "** TODO Added here\n   :PROPERTIES:\n   :ID: task-b\n   :END:\n")

    res = lt.converge(w["b"], root=w["root"])

    ids = _ids(w["b"] / "org" / "next_actions.org")
    for iid in ("task-1", "task-2", "task-a", "task-b"):
        assert ids.count(iid) == 1, (iid, ids)
    assert res.ok, res


def test_org_heading_without_an_id_falls_back_to_line_union(fleet):
    w = fleet
    _both_edit(w, "org/next_actions.org", "* Projects\n",
               "* Projects\nnote from the other host\n", "* Projects\nnote from this host\n")

    res = lt.converge(w["b"], root=w["root"])

    text = (w["b"] / "org" / "next_actions.org").read_text()
    assert text.count("* Projects") == 1, text
    assert "other host" in text and "this host" in text and "<<<<<<<" not in text
    assert _ids(w["b"] / "org" / "next_actions.org").count("task-1") == 1
    assert not res.ok and "org/next_actions.org" in res.reason
    assert len(_conflict_items(w["b"])) == 1


# ─────────────────────────────── alert once, then quiet ───────────────────────────────

def _note_conflict(w: dict) -> None:
    _both_edit(w, "note.md", "two", "two, said elsewhere", "two, said here")


def test_the_second_sync_names_the_waiting_conflict_but_is_not_a_failure(fleet):
    w = fleet
    _note_conflict(w)
    first = lt.converge(w["b"], root=w["root"])
    assert not first.ok and "note.md" in first.reason, first
    (item,) = _conflict_items(w["b"])

    (w["b"] / "later.md").write_text("later\n")
    second = lt.converge(w["b"], root=w["root"])

    assert second.ok, second                         # handled: a task is open for it
    assert "note.md" in second.reason and item.id in second.reason, second
    assert "waiting for a person" in second.reason
    assert not SUCCESS.search(second.reason), second.reason    # SYN-6: no success words
    assert [p for p, _ in second.context["conflicts_handled"]] == ["note.md"]
    assert len(_conflict_items(w["b"])) == 1
    assert lt.converge_line("converge", w["b"], second).startswith(f"converge {SPACE}: waiting"), \
        lt.converge_line("converge", w["b"], second)
    assert lt.converge_line("converge", w["b"], first).startswith(f"converge {SPACE}: FAIL")


def test_another_host_pulling_the_merged_conflict_counts_it_as_handled(fleet):
    w = fleet
    _note_conflict(w)
    lt.converge(w["b"], root=w["root"])              # this host met it and filed the task
    _git(w["a"], "pull", "-q", "--no-rebase", "origin", "main")

    res = lt.converge(w["a"], root=w["root"])

    assert res.ok and "note.md" in res.reason, res


def test_a_conflict_without_a_task_stays_an_alert(fleet, monkeypatch):
    w = fleet
    _note_conflict(w)
    monkeypatch.setattr(lt, "file_conflict_tasks", lambda space, kept: [])   # nothing filed
    lt.converge(w["b"], root=w["root"])

    res = lt.converge(w["b"], root=w["root"])

    assert not res.ok and "note.md" in res.reason, res


def test_fleet_sync_fails_on_the_first_run_and_names_it_quietly_after(fleet, monkeypatch, capsys):
    import git_fleet_sync
    w = fleet
    _note_conflict(w)
    monkeypatch.setattr(sys, "argv", ["git_fleet_sync.py", str(w["root"]), "--execute", "--pull"])

    first = git_fleet_sync.main()
    out1 = capsys.readouterr().out
    (w["b"] / "later.md").write_text("later\n")
    second = git_fleet_sync.main()
    out2 = capsys.readouterr().out

    assert first == 1 and "note.md" in out1, out1
    assert second == 0, out2
    assert "note.md" in out2 and "waiting for a person" in out2, out2
    assert "FAIL" not in out2, out2


def _cos_env(w: dict) -> dict:
    root, tmp = w["root"], w["tmp"]
    lib = root / ".datacore" / "lib"
    if not lib.exists():
        lib.mkdir(parents=True)
        os.symlink(LIB / "ledger_transport.py", lib / "ledger_transport.py")
        (lib / "cos_alert.sh").write_text("#!/bin/sh\necho \"ALERT $*\"\n")
        (lib / "cos_alert.sh").chmod(0o755)
        (tmp / "home" / ".datacore" / "cos").mkdir(parents=True)
    return {**os.environ, "HOME": str(tmp / "home"), "DATACORE_HOME": str(root),
            "DATACORE_ROOT": str(root), "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}"}


def _cos_run(w: dict) -> tuple[str, int]:
    r = subprocess.run(["bash", str(COS_SYNC)], env=_cos_env(w), capture_output=True, text=True, timeout=120)
    line = next((l for l in r.stdout.splitlines() if SPACE in l and "ALERT" not in l), "")
    alerts = sum(1 for l in r.stdout.splitlines() if l.startswith("ALERT") and SPACE in l)
    return line, alerts


def _next_day(w: dict) -> None:
    """The per-space, per-day dedupe files expire at midnight: remove them."""
    for f in (w["tmp"] / "home" / ".datacore" / "cos").glob("sync*-*"):
        if not f.name.startswith("syncconflict-"):
            f.unlink()


def test_cos_sync_alerts_once_for_one_conflict(fleet):
    w = fleet
    _note_conflict(w)

    line1, alerts1 = _cos_run(w)
    _next_day(w)
    (w["b"] / "later.md").write_text("later\n")
    line2, alerts2 = _cos_run(w)
    _next_day(w)
    line3, alerts3 = _cos_run(w)

    assert alerts1 == 1 and "note.md" in line1, (line1, alerts1)
    assert (alerts2, alerts3) == (0, 0), (line2, line3)
    for line in (line2, line3):
        assert "note.md" in line and "waiting for a person" in line, line
        assert "synced clean" not in line and "NEEDS A HUMAN" not in line, line


def test_cos_sync_alerts_once_for_a_conflict_another_sync_met_first(fleet):
    w = fleet
    _note_conflict(w)
    assert not lt.converge(w["b"], root=w["root"]).ok     # the claim loop met it, say

    line1, alerts1 = _cos_run(w)
    _next_day(w)
    line2, alerts2 = _cos_run(w)

    assert alerts1 == 1 and "note.md" in line1, (line1, alerts1)
    assert alerts2 == 0, line2
