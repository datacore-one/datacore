"""SPC-1: Each space's tasks, notes and history stay in that space's own
repository and never land in another space.

Kind: deterministic + production contract (local, read-only).

Deterministic (tmp root with spaces 0-alpha and 1-beta):
  * the ledger boundary (`org_space.ledger_space_for_file`) maps an org file to
    its own space, and refuses an org file in 0-alpha that is a symlink into
    1-beta (a crossing alias), and a file outside any org/ tree;
  * the hourly sweep (`ledger_ingest_org.main`) records each space's tasks in
    that space's history only, even when both spaces hold a task with the same
    heading;
  * an agent's history write for a task (nightshift `ledger_hooks.emit`) lands
    in the task's own space, never in the space the process started in.

Production (@production, local): no space's content is tracked by another
repository -- the root repo tracks nothing under a space directory, and no
space repo tracks a path inside another space's directory.

Seeded failure: an ancestor-events fallback in `ledger_space_for_file` (a file
with no ledger in its own space borrowing the nearest ancestor's events dir),
which is how a symlinked org file would file its tasks into the wrong space.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import ledger_ingest_org as ingest
from ledger.log import read_events
from org_space import ledger_space_for_file

ROOT = Path(__file__).resolve().parents[3]
NIGHTSHIFT_LIB = ROOT / ".datacore" / "modules" / "nightshift" / "lib"


@pytest.fixture
def root(tmp_path, monkeypatch):
    root = tmp_path / "Data"
    for name, tid in (("0-alpha", "a1"), ("1-beta", "b1")):
        (root / name / "org").mkdir(parents=True)
        (root / name / ".datacore" / "events").mkdir(parents=True)
        (root / name / "org" / "next_actions.org").write_text(
            f"* NEXT Same heading\n:PROPERTIES:\n:ID: {tid}\n:END:\n")
    monkeypatch.setattr(ingest, "_this_actor", lambda: "sweeper")
    monkeypatch.setattr(ingest, "_notify_daemon", lambda root: None)
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    return root


def _ids(space: Path) -> set:
    return {(e.payload or {}).get("id") for e in read_events(space)}


def test_the_ledger_boundary_is_the_files_own_space(root):
    alpha, beta = root / "0-alpha", root / "1-beta"
    assert ledger_space_for_file(alpha / "org" / "next_actions.org") == alpha.resolve()
    assert ledger_space_for_file(beta / "org" / "next_actions.org") == beta.resolve()
    alias = alpha / "org" / "borrowed.org"
    os.symlink(beta / "org" / "next_actions.org", alias)
    assert ledger_space_for_file(alias) is None, "a symlink into another space was accepted"
    (root / "loose.org").write_text("* TODO loose\n")
    assert ledger_space_for_file(root / "loose.org") is None


def test_the_sweep_keeps_each_spaces_tasks_at_home(root, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ledger_ingest_org.py", "--root", str(root)])
    assert ingest.main() == 0
    assert _ids(root / "0-alpha") == {"a1"}
    assert _ids(root / "1-beta") == {"b1"}


def test_an_agents_history_write_lands_in_the_tasks_space(root, monkeypatch, tmp_path):
    if str(NIGHTSHIFT_LIB) not in sys.path:
        sys.path.insert(0, str(NIGHTSHIFT_LIB))
    import importlib.util
    spec = importlib.util.spec_from_file_location("ns_hooks_spc1", NIGHTSHIFT_LIB / "ledger_hooks.py")
    hooks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hooks)
    monkeypatch.setenv("DATACORE_ACTOR", "nightshift")
    monkeypatch.chdir(root / "0-alpha")          # the process started in another space
    task = SimpleNamespace(id="b1", file_path=str(root / "1-beta" / "org" / "next_actions.org"))
    assert hooks.emit(task, "item.clock.start", exec_id="e1")
    assert "b1" in _ids(root / "1-beta")
    assert "b1" not in _ids(root / "0-alpha")


@pytest.mark.production
def test_no_repository_tracks_another_spaces_content():
    spaces = sorted(p.name for p in ROOT.glob("[0-9]-*") if (p / ".git").exists())
    assert spaces, "no space repositories found (could not check)"
    problems = []
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--", "[0-9]-*"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        problems.append("root: could not list (could not check)")
    elif r.stdout.strip():
        problems.append(f"root repo tracks space content: {r.stdout.split()[:3]}")
    other = re.compile(r"(^|/)(" + "|".join(map(re.escape, spaces)) + r")/")
    for name in spaces:
        r = subprocess.run(["git", "-C", str(ROOT / name), "ls-files"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            problems.append(f"{name}: could not list (could not check)")
            continue
        foreign = [p for p in r.stdout.splitlines()
                   if (m := other.search(p)) and m.group(2) != name]
        if foreign:
            problems.append(f"{name} tracks {len(foreign)} path(s) of another space, e.g. {foreign[0]}")
    assert not problems, "; ".join(problems)
