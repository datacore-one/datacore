"""The wrap-up audit scores this session's files, not the whole disk.

2026-09-07: a wrap-up that had committed and pushed everything it touched
scored 4/6 because five project checkouts and the owner's app settings
were dirty. Those belong to other sessions; they are reported, not scored."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wrap_up_mechanics as wm  # noqa: E402


def _root(tmp_path, monkeypatch):
    root = tmp_path / "Data"
    for d in (root / ".git", root / "0-personal" / ".git", root / "2-datacore" / ".git",
              root / "5-plur" / "2-projects" / "plur" / ".git"):
        d.mkdir(parents=True)
    monkeypatch.setattr(wm, "DATACORE_ROOT", root)
    monkeypatch.setattr(wm, "spaces", lambda: [root / "0-personal", root / "2-datacore"])
    return root


REPOS = [
    {"repo": ".", "dirty_files": 2, "unpushed_commits": 0},
    {"repo": "0-personal", "dirty_files": 0, "unpushed_commits": 0},
    {"repo": "2-datacore", "dirty_files": 1, "unpushed_commits": 0},
    {"repo": "5-plur/2-projects/plur", "dirty_files": 3, "unpushed_commits": 2},
]


def test_session_files_clean_and_pushed_passes_despite_other_dirt(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    monkeypatch.setattr(wm, "git_dirty", lambda repo: {"app-settings.json"} if repo == root else set())
    mine = [str(root / "0-personal" / "org" / "inbox.org"),
            str(root / "5-plur" / "2-projects" / "plur" / "src" / "x.ts")]
    own, others = wm.session_scope_rows(mine, REPOS)
    assert own["ok"] is True
    assert "1 repo(s) this session touched are clean and pushed" in own["detail"]
    assert "1 file(s) in project repos" in own["detail"]
    assert "dirty: ['.', '2-datacore', '5-plur/2-projects/plur']" in others
    assert "not scored" in others


def test_a_session_file_still_dirty_fails_and_names_it(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    monkeypatch.setattr(wm, "git_dirty", lambda repo: {"journal/2026-09-07.md"} if repo.name == "2-datacore" else set())
    mine = [str(root / "2-datacore" / "journal" / "2026-09-07.md")]
    own, _ = wm.session_scope_rows(mine, REPOS)
    assert own["ok"] is False
    assert own["detail"] == "uncommitted 2-datacore: ['journal/2026-09-07.md']"


def test_an_unpushed_session_repo_fails(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    monkeypatch.setattr(wm, "git_dirty", lambda repo: set())
    repos = [dict(r) for r in REPOS]
    repos[1]["unpushed_commits"] = 2
    own, _ = wm.session_scope_rows([str(root / "0-personal" / "org" / "inbox.org")], repos)
    assert own["ok"] is False and own["detail"] == "unpushed 0-personal: 2 commit(s)"
