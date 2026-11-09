"""SPC-9: A new space, once registered in one place, is picked up by sync, the
ledger and context without editing Datacore code.

Kind: deterministic. A tmp install that already has a space (0-personal,
registered everywhere it needs to be) gains an eleventh space, `10-studio`
(0-9 are taken in this fleet, so the next space has two digits). It is
registered in ONE place -- its space marker `.datacore/config.yaml`, which
`spaces.discover_spaces` reads -- and is a git clone with an origin, org files
and context layers. Nothing else is edited.

Promise, as evals, each asking the real component:
  * discovery: `spaces.discover_spaces(root)` lists it (the one place works);
  * sync: `ledger_transport.sync_outcomes(root)` syncs it (not "skipped"),
    and `converge` accepts it;
  * ledger: the hourly sweep (`ledger_ingest_org.main --root`) records its task;
  * context: `context_merge.rebuild_all(root)` composes its CLAUDE.md.

Seeded failure: audit C12 -- the transport refuses any repo absent from the
tracked registry/repositories.yaml (a second registration), and several
components enumerate spaces with the one-digit glob `[0-9]-*`.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import context_merge
import ledger_ingest_org as ingest
import ledger_transport as lt
import spaces
from ledger.log import read_events

NEW = "10-studio"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    monkeypatch.setenv("DATACORE_ACTOR", "tester")
    root = tmp_path / "Data"
    (root / ".datacore" / "registry").mkdir(parents=True)
    (root / ".datacore" / "registry" / "repositories.yaml").write_text(
        "repositories:\n  0-personal:\n    category: knowledge\n")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for name, kind in (("0-personal", "personal"), (NEW, "team")):
        origin = tmp_path / f"{name}.git"
        subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True, timeout=60)
        sp = root / name
        subprocess.run(["git", "init", "-q", "-b", "main", str(sp)], check=True, timeout=60)
        for k, v in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", str(hooks))):
            _git(sp, "config", k, v)
        _git(sp, "remote", "add", "origin", str(origin))
        (sp / ".datacore").mkdir()
        (sp / ".datacore" / "config.yaml").write_text(f"space:\n  name: {name.split('-', 1)[1]}\n  type: {kind}\n")
        (sp / ".gitignore").write_text("CLAUDE.md\nAGENTS.md\nGEMINI.md\n*.local.md\n")
        (sp / "org").mkdir()
        (sp / "org" / "next_actions.org").write_text(
            f"* NEXT First task of {name}\n:PROPERTIES:\n:ID: {name}-t1\n:END:\n")
        (sp / "CLAUDE.base.md").write_text(f"# {name}\n")
        _git(sp, "add", "-A")
        _git(sp, "commit", "-qm", "seed")
        assert _git(sp, "push", "-q", "-u", "origin", "main").returncode == 0
    monkeypatch.setattr(ingest, "_this_actor", lambda: "tester")
    monkeypatch.setattr(ingest, "_notify_daemon", lambda root: None)
    return root


def test_discovery_lists_the_new_space(root):
    names = {s.path.name for s in spaces.discover_spaces(root)}
    assert NEW in names, f"the space marker is not enough to be discovered: {sorted(names)}"


def test_sync_picks_up_the_new_space(root):
    outcomes = {name: outcome for name, _, outcome in lt.sync_outcomes(root, include_code=False)}
    res = lt.converge(root / NEW, root=root)
    assert outcomes.get(NEW) == "clean" and res.ok, (
        f"sync did not pick up {NEW}: sync_outcomes={outcomes}, converge={res.reason!r}")


def test_the_ledger_sweep_picks_up_the_new_space(root, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ledger_ingest_org.py", "--root", str(root)])
    ingest.main()
    ids = {(e.payload or {}).get("id") for e in read_events(root / NEW)}
    assert f"{NEW}-t1" in ids, f"the hourly sweep never recorded {NEW}'s task"
    assert "0-personal-t1" in {(e.payload or {}).get("id") for e in read_events(root / "0-personal")}


def test_context_is_composed_for_the_new_space(root):
    context_merge.rebuild_all(root)
    assert (root / NEW / "CLAUDE.md").is_file(), f"no composed context for {NEW}"
