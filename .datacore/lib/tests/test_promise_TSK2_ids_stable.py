"""TSK-2: A task keeps the same identity forever. Something I dismissed or
finished never comes back as open.

Kind: deterministic. Real code against a tmp space: the org-workspace library
as installed (what every script and the app import), the core adapter, and the
desktop app's write path (datacored.adapters.org.set_state).

The mechanism behind the owner's report (2026-09-26): two headings carry the
same :ID: (a stale copy, a sync merge). org-workspace's load() silently gives
the second one a fresh random id in memory (dedup_ids); the next unrelated save
writes that id to disk. The copy is now a "new" task, open, that no dismissal
ever covered.

Seeded failure: a file with an open copy of a task sharing the id of the task
the owner dismisses. The dismissal must not mint a new open task, and loading
must never change an id. Red today on the library default and the app write path.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
ROOT = LIB.parent.parent
APP = ROOT / "2-datacore" / "2-projects" / "datacore-app" / "daemon"
sys.path.insert(0, str(LIB))

ADAPTER = LIB / "org_workspace_adapter.py"
HEADER = "#+SEQ_TODO: TODO(t) NEXT(n) WAITING(w@) REVIEW(r!) | DONE(d!) DEFERRED(f@) CANCELLED(c@)\n"
OPEN = {"TODO", "NEXT", "WAITING", "REVIEW"}


def _task(state: str, title: str, tid: str) -> str:
    return f"* {state} {title}\n:PROPERTIES:\n:ID: {tid}\n:END:\n"


def _dup_file(root: Path) -> Path:
    org = root / "5-evals" / "org"
    org.mkdir(parents=True)
    f = org / "next_actions.org"
    f.write_text(HEADER
                 + _task("TODO", "Write the report", "task-report")
                 + _task("TODO", "Call the bank", "task-bank")
                 + _task("TODO", "Call the bank", "task-bank"),   # a stale copy
                 encoding="utf-8")
    return f


def _on_disk(f: Path) -> list[tuple[str, str]]:
    """[(state, id)] per heading, read straight from the bytes."""
    out, state = [], None
    for line in f.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\*+\s+([A-Z]+)\s", line)
        if m:
            state = m.group(1)
        m = re.match(r"^:ID:\s+(\S+)", line)
        if m:
            out.append((state, m.group(1)))
    return out


def test_the_library_never_changes_an_id_while_loading(tmp_path):
    """GT-09: plain OrgWorkspace (the default every script gets) must keep ids
    or refuse; regenerating one is the start of every resurrection."""
    from org_workspace import OrgWorkspace
    f = _dup_file(tmp_path)
    before = sorted(i for _, i in _on_disk(f))
    try:
        ws = OrgWorkspace()
        ws.load(f)
    except Exception:
        return  # refusing a duplicate is keeping identity
    loaded = sorted(n.id() for n in ws.all_nodes() if n.id())
    assert loaded == before, (
        f"load() rewrote identity in memory: file has {before}, workspace has {loaded}")


def test_the_core_adapter_refuses_a_duplicate_and_leaves_the_file(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    f = _dup_file(tmp_path)
    before = f.read_bytes()
    r = subprocess.run([sys.executable, str(ADAPTER), "complete", "--file", str(f),
                        "--id", "task-report"], capture_output=True, text=True, timeout=60)
    out = json.loads(r.stdout or "{}")
    assert r.returncode != 0 or out.get("error"), "a file with a duplicate id was edited"
    assert f.read_bytes() == before


def _app():
    sys.path.insert(0, str(APP))
    from datacored.adapters import org as app_org
    app_org.invalidate_cache()
    return app_org


def test_reading_tasks_in_the_app_never_writes(tmp_path):
    app_org = _app()
    f = _dup_file(tmp_path)
    before = f.read_bytes()
    app_org.list_all_tasks(tmp_path / "5-evals", include_done=True)
    assert f.read_bytes() == before


def test_dismissing_a_task_never_brings_a_copy_back_as_a_new_open_task(tmp_path):
    """The owner dismisses 'Call the bank' in the app. Afterwards no heading may
    carry an id that was not there before, and nothing with that id is open
    under a new name. Either the app refuses the file (duplicate) or it
    dismisses the one task; it may never mint a new identity."""
    app_org = _app()
    f = _dup_file(tmp_path)
    ids_before = {i for _, i in _on_disk(f)}
    try:
        app_org.set_state(tmp_path / "5-evals", "task-bank", "CANCELLED")
    except Exception:
        pass  # refusing is fine
    after = _on_disk(f)
    minted = [(s, i) for s, i in after if i not in ids_before]
    assert not minted, (
        f"dismissing task-bank minted new identities {minted}: the stale copy is now "
        "a different, open task the dismissal never covered")


def test_an_unrelated_edit_never_persists_a_regenerated_id(tmp_path):
    """Any script that edits one task through the library and saves must not
    write a new id onto a different task."""
    from org_workspace import OrgWorkspace
    f = _dup_file(tmp_path)
    ids_before = sorted(i for _, i in _on_disk(f))
    try:
        ws = OrgWorkspace()
        ws.load(f)
        node = ws.find_by_id("task-report")
        ws.transition(node, "DONE")
        ws.save()
    except Exception:
        pass
    assert sorted(i for _, i in _on_disk(f)) == ids_before, (
        "completing task-report rewrote another task's :ID: on disk")
