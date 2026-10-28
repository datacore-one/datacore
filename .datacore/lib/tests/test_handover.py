"""/handover: move a running Mac session to dayshift.

Spec: 2-datacore/1-tracks/dev/superpowers/specs/2026-10-28-handover-design.md
Plan: 2-datacore/1-tracks/dev/superpowers/plans/2026-10-28-handover.md
"""
from __future__ import annotations

import json

import pytest

from pathlib import Path

from handover import (
    blocking_issues,
    group_of,
    local_preview_path,
    new_entries,
    opener,
    permission_args,
    repo_action,
    session_is_live,
    tmux_name,
    valid_preview,
    write_checkpoint,
)


def test_permission_args_keeps_flags_and_mode_drops_chrome():
    assert permission_args(["claude", "--allow-dangerously-skip-permissions", "--chrome"], "auto") == (
        ["--allow-dangerously-skip-permissions", "--permission-mode", "auto"], ["--chrome"])


def test_permission_args_no_mode_adds_nothing():
    assert permission_args(["claude"], None) == ([], [])


def test_permission_args_never_carries_resume_or_session_flags():
    # The Mac session may itself have been resumed; dayshift gets its own --resume.
    assert permission_args(["claude", "--resume", "abc", "--permission-mode", "auto"], None) == (
        ["--permission-mode", "auto"], [])


def test_session_is_live():
    assert session_is_live(1, lambda p: "claude --chrome") is True
    assert session_is_live(1, lambda p: None) is False
    assert session_is_live(1, lambda p: "vim") is False


@pytest.mark.parametrize("args,expected", [
    ((False, False, True, False), "stop:missing"),
    ((True, True, True, False), "ok"),
    ((True, True, False, False), "ok"),
    ((True, False, True, True), "fast-forward"),
    # Dirty but fast-forwardable: try it. `git merge --ff-only` refuses on its own
    # when an incoming commit touches a locally changed file, and never overwrites one.
    ((True, False, False, True), "fast-forward"),
    ((True, False, True, False), "stop:diverged"),
])
def test_repo_action(args, expected):
    assert repo_action(*args) == expected


def test_tmux_name():
    assert tmux_name("Evals: promise run #3") == "evals-promise-run-3"
    assert tmux_name("!!!") == "handover"
    assert len(tmux_name("x" * 80)) <= 30


def test_blocking_issues_from_finalize_report():
    # Anything finalize could not push would be missing on dayshift: the handover stops.
    clean = {"pushes": [{"repo": ".", "ok": True}, {"repo": "0-personal", "ok": None}],
             "skipped_project_repos": [], "unversioned": ["/home/user/.zshrc"]}
    assert blocking_issues(clean) == []
    failed = {"pushes": [{"repo": "2-datacore", "ok": False, "note": "push failed"}],
              "skipped_project_repos": ["/home/user/Data/5-x/2-projects/app/a.ts"]}
    issues = blocking_issues(failed)
    assert len(issues) == 2
    assert "2-datacore" in issues[0] and "push failed" in issues[0]
    assert "a.ts" in issues[1]
    assert blocking_issues({"error": "session not archived", "pushes": []}) == ["finalize: session not archived"]


def test_checkpoint_roundtrip(tmp_path):
    p = write_checkpoint("abc", 42, "auto", "evals", [{"what": "a", "progress": "b", "relaunch": "c"}],
                         [{"path": "2-datacore", "branch": "main", "sha": "f00"}], tmp_path)
    assert p == tmp_path / "abc.json"
    data = json.loads(p.read_text())
    assert data["claude_pid"] == 42
    assert data["mode"] == "auto"
    assert data["repos"][0]["sha"] == "f00"
    assert data["inflight"][0]["relaunch"] == "c"


# --- previews: files a dayshift session opens appear on the Mac -------------

def test_valid_preview(tmp_path):
    (tmp_path / "a b;$(x).md").write_text("x")
    (tmp_path / "run.sh").write_text("x")
    assert valid_preview(str(tmp_path / "a b;$(x).md"), tmp_path) == tmp_path / "a b;$(x).md"
    assert valid_preview(str(tmp_path / ".." / "etc.md"), tmp_path) is None
    assert valid_preview(str(tmp_path / "run.sh"), tmp_path) is None
    assert valid_preview(str(tmp_path / "missing.md"), tmp_path) is None
    assert valid_preview(str(tmp_path / "a b;$(x).md") + "\n", tmp_path) is None


def test_local_preview_path():
    assert local_preview_path(Path("/home/user/Data/0-personal/n.md"), Path("/home/user/Data"),
                              Path("/c")) == Path("/c/0-personal/n.md")


def test_local_preview_path_refuses_outside_root():
    with pytest.raises(ValueError):
        local_preview_path(Path("/home/user/Data/../.ssh/id.md"), Path("/home/user/Data"), Path("/c"))


def test_opener():
    assert opener(Path("/c/n.md")) == ["open", "-a", "Typora", "/c/n.md"]
    assert opener(Path("/c/b.html")) == ["open", "/c/b.html"]


def test_new_entries_replays_once():
    lines = ["a", "b", "c"]
    assert new_entries(lines, 1) == (["b", "c"], 3)
    assert new_entries(lines, 3) == ([], 3)
    assert new_entries(lines, 9) == (["a", "b", "c"], 3)   # queue was reset: replay it


def test_group_of():
    out = "dev \nplur-new-7 plur-new\nnowva-8 nowva\n"
    assert group_of(out, "plur-new") == "plur-new"
    assert group_of(out, "plur-new-7") == "plur-new"
    assert group_of(out, "dev") == "dev"
    assert group_of(out, "zzz") is None
