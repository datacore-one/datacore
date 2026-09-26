"""SYN-8: A file a safety check refuses is named to me, and everything else in
that space, including its history, still syncs.

Kind: deterministic. A space clone with a local bare origin and a pre-commit
hook that refuses exactly one file (org/inbox.org, the 2026-09-26 shape: an
inbox capture with an invalid tag). The same space also holds:
  * another edited note, and a new ledger event (to send);
  * a commit waiting on origin from another machine (to receive).

After one sync, via `ledger_transport.converge` and via
`git_fleet_sync.main --execute --pull`:
  * the refused file is named in what the sync reports;
  * the note and the ledger event are on origin;
  * origin's commit is in the local history;
  * the refused file is still in the working tree, unchanged (never lost).

Seeded failure: LS-11 / audit B-F5 -- "a refused autosave must stop the
converge" (test_ledger_transport.py::test_refused_autosave_stops_the_converge
pins it): one refused inbox capture stopped 2-datacore's converge hourly from
2026-09-26 04:25Z, so nothing else in the space moved.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ledger.log import EventLog, read_events

HOOK = """#!/bin/sh
if git diff --cached --name-only | grep -q '^org/inbox.org$'; then
  echo 'INVALID ORG TAGS: org/inbox.org line 3 (:bad tag:)' >&2
  exit 1
fi
exit 0
"""
BAD_INBOX = "* TODO capture\n* TODO another\n* TODO tagged :bad tag:\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


@pytest.fixture
def world(tmp_path, monkeypatch):
    import actor_identity
    reg = tmp_path / "principals.yaml"
    reg.write_text("principals:\n  p:\n    kind: agent\n    writes_as: [tester]\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", reg)
    monkeypatch.setenv("DATACORE_ACTOR", "tester")
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True, timeout=60)
    root = tmp_path / "Data"
    (root / ".datacore" / "registry").mkdir(parents=True)
    (root / ".datacore" / "registry" / "repositories.yaml").write_text(
        "repositories:\n  9-fixture:\n    category: knowledge\n")
    sp = root / "9-fixture"
    subprocess.run(["git", "clone", "-q", str(origin), str(sp)], check=True, timeout=60)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", str(hooks))):
        _git(sp, "config", k, v)
    (sp / "org").mkdir()
    (sp / "org" / "inbox.org").write_text("* TODO capture\n")
    (sp / "note.md").write_text("old\n")
    _git(sp, "add", "-A")
    _git(sp, "commit", "-qm", "seed")
    assert _git(sp, "push", "-q", "-u", "origin", "HEAD:main").returncode == 0
    _git(sp, "remote", "set-head", "origin", "main")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, timeout=60)
    for k, v in (("user.email", "o@t"), ("user.name", "o"), ("core.hooksPath", str(tmp_path / "none"))):
        _git(other, "config", k, v)
    (other / "from-box.md").write_text("written on the box\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "box work")
    assert _git(other, "push", "-q", "origin", "main").returncode == 0
    # Local state: one refused file, one good edit, one ledger event.
    (hooks / "pre-commit").write_text(HOOK)
    (hooks / "pre-commit").chmod(0o755)
    (sp / "org" / "inbox.org").write_text(BAD_INBOX)
    (sp / "note.md").write_text("edited here\n")
    EventLog(sp, "tester", sign=False).append("item.create", {"id": "t1", "title": "t", "state": "NEXT"})
    return root, sp, origin


def _origin_has(origin: Path, path: str, text: str) -> bool:
    r = subprocess.run(["git", "-C", str(origin), "show", f"main:{path}"],
                       capture_output=True, text=True, timeout=30)
    return r.returncode == 0 and text in r.stdout


def _assert_rest_synced(sp: Path, origin: Path, report: str) -> None:
    problems = []
    if "org/inbox.org" not in report:
        problems.append("the refused file is not named")
    if not _origin_has(origin, "note.md", "edited here"):
        problems.append("the other edit did not reach origin")
    if not _origin_has(origin, ".datacore/events/tester.jsonl", '"id":"t1"'):
        problems.append("the ledger event did not reach origin")
    if not (sp / "from-box.md").exists():
        problems.append("origin's commit did not reach this machine")
    if (sp / "org" / "inbox.org").read_text() != BAD_INBOX:
        problems.append("the refused file was changed or lost")
    assert not problems, "; ".join(problems) + f" -- report: {report[:300]!r}"


def test_converge_names_the_refused_file_and_syncs_the_rest(world):
    import ledger_transport as lt
    root, sp, origin = world
    res = lt.converge(sp, root=root)
    _assert_rest_synced(sp, origin, f"{res.reason} {res.context}")


def test_fleet_sync_names_the_refused_file_and_syncs_the_rest(world, monkeypatch, capsys):
    import git_fleet_sync
    root, sp, origin = world
    monkeypatch.setattr(sys, "argv", ["git_fleet_sync.py", str(root), "--execute", "--pull"])
    git_fleet_sync.main()
    _assert_rest_synced(sp, origin, capsys.readouterr().out)
