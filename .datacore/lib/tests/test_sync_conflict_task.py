"""A content conflict found while syncing a space becomes ONE task for a person,
and the rest of the space keeps syncing (SYN-9; owner decision 2026-09-30).

Owner, 2026-09-30: "let's avoid creating multiple copies and excessive backups.
If there are conflicting files, the conflict should be resolved. It can be a
task on its own, even." So: no copy files, no side branches; both versions stay
in git history and in the one file (git's `union` resolution, already used
fleet-wide for journals through .gitattributes), and the task names the space,
the file and the two commits. It is filed once per conflict, not once per sync.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import ledger_transport as lt
from ledger.fold import fold
from ledger.log import read_events


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


@pytest.fixture
def pair(tmp_path, monkeypatch):
    import actor_identity
    reg = tmp_path / "principals.yaml"
    reg.write_text("principals:\n  pa:\n    kind: agent\n    writes_as: [a]\n"
                   "  pb:\n    kind: agent\n    writes_as: [b]\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", reg)
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    monkeypatch.setenv("DATACORE_ACTOR", "b")
    monkeypatch.setattr(lt, "classify",
                        lambda space, root=None: lt.Result(True, "knowledge", {"entry": {"category": "knowledge"}}))
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True, timeout=60)
    clones = {}
    for name in ("a", "b"):
        path = tmp_path / name / "9-fixture"
        path.parent.mkdir()
        if name == "a":
            subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, timeout=60)
            _git(path, "remote", "add", "origin", str(origin))
        else:
            subprocess.run(["git", "clone", "-q", str(origin), str(path)], check=True, timeout=60)
        for k, v in (("user.email", f"{name}@t"), ("user.name", name), ("core.hooksPath", str(hooks))):
            _git(path, "config", k, v)
        if name == "a":
            (path / ".gitignore").write_text(".datacore/state/*\n")
            (path / "note.md").write_text("one\ntwo\nthree\n")
            (path / "pic.bin").write_bytes(b"\x00base\x00")
            _git(path, "add", "-A")
            _git(path, "commit", "-qm", "seed")
            assert _git(path, "push", "-q", "-u", "origin", "main").returncode == 0
        clones[name] = path
    _git(clones["b"], "pull", "-q", "origin", "main")
    return clones["a"], clones["b"], origin


def _conflict(a: Path, b: Path, rel: str = "note.md", va: bytes = b"one\nA says two\nthree\n",
              vb: bytes = b"one\nB says two\nthree\n") -> tuple[str, str]:
    (a / rel).write_bytes(va)
    _git(a, "commit", "-qam", "A's version")
    assert _git(a, "push", "-q", "origin", "main").returncode == 0
    (b / rel).write_bytes(vb)
    _git(b, "commit", "-qam", "B's version")
    return (_git(b, "rev-parse", "HEAD").stdout.strip(),
            _git(a, "rev-parse", "HEAD").stdout.strip())


def _conflict_items(space: Path) -> list:
    return [i for i in fold(read_events(space)).items.values()
            if isinstance((i.payload or {}).get("sync_conflict"), dict)]


def test_a_conflict_is_one_task_naming_space_file_and_both_commits(pair):
    a, b, origin = pair
    ours, theirs = _conflict(a, b)
    (b / "other.md").write_text("unrelated\n")

    res = lt.converge(b)

    assert not res.ok and "conflict" in res.reason and "note.md" in res.reason, res
    items = _conflict_items(b)
    assert len(items) == 1, items
    text = f"{items[0].title}\n{items[0].payload.get('body', '')}"
    for needed in ("9-fixture", "note.md", ours[:12], theirs[:12]):
        assert needed in text, f"{needed!r} missing from the task: {text!r}"
    # the task travelled with the rest of the space
    shown = subprocess.run(["git", "-C", str(origin), "show", "main:.datacore/events/b.jsonl"],
                           capture_output=True, text=True).stdout
    assert items[0].id in shown
    # no copies, no side branches
    assert _git(b, "for-each-ref", "--format=%(refname)", "refs/heads").stdout.split() == ["refs/heads/main"]
    assert not [p for p in b.rglob("*") if p.suffix in (".orig", ".bak", ".rej")]


def test_the_next_sync_names_it_again_but_files_no_second_task(pair):
    a, b, _ = pair
    _conflict(a, b)
    lt.converge(b)
    (b / "later.md").write_text("later\n")

    res = lt.converge(b)

    assert "note.md" in res.reason, res
    assert len(_conflict_items(b)) == 1
    creates = [e for e in read_events(b) if e.type == "item.create"
               and isinstance(e.payload.get("sync_conflict"), dict)]
    assert len(creates) == 1, "the task was re-filed"


def test_once_a_person_edits_the_file_it_is_no_longer_reported(pair):
    a, b, _ = pair
    _conflict(a, b)
    lt.converge(b)
    (b / "note.md").write_text("one\nB says two, agreed with A\nthree\n")

    res = lt.converge(b)

    assert res.ok, res
    assert "note.md" not in res.reason


def test_a_binary_conflict_keeps_this_hosts_copy_and_still_flows(pair):
    a, b, origin = pair
    ours, theirs = _conflict(a, b, "pic.bin", b"\x00A\x00", b"\x00B\x00")
    (b / "other.md").write_text("unrelated\n")

    res = lt.converge(b)

    assert "pic.bin" in res.reason, res
    assert (b / "pic.bin").read_bytes() == b"\x00B\x00"
    assert not (b / ".git" / "MERGE_HEAD").exists()
    assert not _git(b, "ls-files", "--unmerged").stdout.strip()
    assert subprocess.run(["git", "-C", str(origin), "show", "main:other.md"],
                          capture_output=True).returncode == 0
    assert len(_conflict_items(b)) == 1
