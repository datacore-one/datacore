"""/handover: move a running Mac session to dayshift.

Spec: 2-datacore/1-tracks/dev/superpowers/specs/2026-10-28-handover-design.md
Plan: 2-datacore/1-tracks/dev/superpowers/plans/2026-10-28-handover.md
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from handover import (
    blocking_issues,
    group_of,
    local_preview_path,
    new_entries,
    opener,
    permission_args,
    remote_copy_action,
    remote_preview_command,
    repo_action,
    safe_group,
    session_is_live,
    title_sequence,
    tmux_name,
    unversioned_blocks,
    valid_preview,
    write_checkpoint,
)


# --- permissions: the resumed session is never wider than the Mac one --------

def test_permission_args_keeps_skip_flag_and_mode_drops_the_rest():
    assert permission_args(["claude", "--allow-dangerously-skip-permissions", "--chrome"], "auto") == (
        ["--allow-dangerously-skip-permissions", "--permission-mode", "auto"], ["--chrome"])


def test_permission_args_no_mode_adds_nothing():
    assert permission_args(["claude"], None) == ([], [])


def test_permission_args_never_carries_session_flags_or_prompts():
    keep, dropped = permission_args(["claude", "--resume", "abc", "fix the bug", "--permission-mode", "plan"], None)
    assert keep == ["--permission-mode", "plan"]
    assert "--resume" in dropped and "abc" not in keep and "fix the bug" not in keep


def test_permission_args_bare_resume_does_not_swallow_the_next_flag():
    assert permission_args(["claude", "--resume", "--permission-mode", "plan"], None)[0] == [
        "--permission-mode", "plan"]


def test_permission_args_equals_forms():
    keep, dropped = permission_args(["claude", "--permission-mode=plan", "--resume=abc"], None)
    assert keep == ["--permission-mode", "plan"] and "--resume=abc" in dropped


def test_permission_args_live_mode_wins_over_launch_mode():
    assert permission_args(["claude", "--permission-mode", "plan"], "auto")[0] == ["--permission-mode", "auto"]


def test_permission_args_refuses_bypass_the_mac_session_could_not_have():
    with pytest.raises(ValueError):
        permission_args(["claude"], "bypassPermissions")
    assert permission_args(["claude", "--dangerously-skip-permissions"], "bypassPermissions")[0] == [
        "--dangerously-skip-permissions", "--permission-mode", "bypassPermissions"]
    # launched straight into bypass, without a skip flag
    assert permission_args(["claude", "--permission-mode", "bypassPermissions"], None)[0] == [
        "--permission-mode", "bypassPermissions"]


def test_permission_args_skip_flag_never_forces_bypass_over_another_mode():
    # The Mac session was switched to auto; --dangerously-skip-permissions on dayshift could
    # start it in bypass instead. Carry the "bypass is available" form.
    assert permission_args(["claude", "--dangerously-skip-permissions"], "auto")[0] == [
        "--allow-dangerously-skip-permissions", "--permission-mode", "auto"]


def test_permission_args_refuses_any_flag_not_known_safe_to_drop():
    # --tools, --strict-mcp-config, --setting-sources and the like restrict the session.
    for argv in (["claude", "--tools", "Read"], ["claude", "--strict-mcp-config"],
                 ["claude", "--setting-sources=user"]):
        with pytest.raises(ValueError):
            permission_args(argv, None)
    assert permission_args(["claude", "--model", "opus", "--chrome", "-c"], None) == (
        [], ["--model", "--chrome", "-c"])


def test_permission_args_refuses_unknown_mode():
    with pytest.raises(ValueError):
        permission_args(["claude"], "yolo")


# --- the Mac session, dayshift's repos and its copy of the conversation -------

def test_session_is_live():
    assert session_is_live(1, lambda p: "claude --chrome") is True
    assert session_is_live(1, lambda p: None) is False
    assert session_is_live(1, lambda p: "vim") is False


@pytest.mark.parametrize("args,expected", [
    ((False, False, True, False), "stop:missing"),
    ((True, True, True, False), "ok"),
    ((True, False, True, True), "fast-forward"),
    ((True, False, True, False), "stop:diverged"),
    # dayshift on another branch: never move that branch, even if it would fast-forward
    ((True, False, False, True), "stop:other-branch"),
    ((True, True, False, False), "stop:other-branch"),
    # local edits there would block a commit coming in: stop before changing any repo
    ((True, False, True, True, True), "stop:local-edits"),
    ((True, True, True, False, True), "ok"),
])
def test_repo_action(args, expected):
    assert repo_action(*args) == expected


@pytest.mark.parametrize("args,expected", [
    ((False, None, "h1", None), "copy"),
    ((False, "h1", "h1", "h1"), "skip"),          # same bytes already there: a rerun after a failed attach
    ((False, "p", "h1", "p"), "copy"),            # an interrupted copy: a prefix of the Mac file
    ((False, "h2", "h1", "x"), "stop:diverged"),  # dayshift's copy moved on: never overwrite it
    ((True, "h1", "h1", "h1"), "stop:live"),      # already running there: a second copy would fork it
])
def test_remote_copy_action(args, expected):
    assert remote_copy_action(*args) == expected


def test_tmux_name():
    assert tmux_name("Evals: promise run #3") == "evals-promise-run-3"
    assert tmux_name("!!!") == "handover"
    assert len(tmux_name("x" * 80)) <= 30


def test_blocking_issues_from_finalize_report(tmp_path):
    data = tmp_path / "Data"
    clean = {"pushes": [{"repo": ".", "ok": True}, {"repo": "0-personal", "ok": None}],
             "skipped_project_repos": [], "unversioned": [str(tmp_path / ".zshrc")]}
    assert blocking_issues(clean, data) == []          # outside Data: reported, not blocking
    failed = {"pushes": [{"repo": "2-datacore", "ok": False, "note": "push failed"},
                         {"repo": "0-personal", "ok": True, "deleted_not_staged": ["gone.md"]}],
              "unversioned": [str(data / ".datacore/modules/m/lib/x.py")]}
    issues = blocking_issues(failed, data)
    assert len(issues) == 2                          # unversioned is judged by unversioned_blocks
    assert "2-datacore" in issues[0] and "push failed" in issues[0]
    assert "gone.md" in issues[1]
    assert blocking_issues({"error": "session not archived", "pushes": []}, data) == [
        "finalize: session not archived"]


def test_unversioned_blocks_only_for_a_nested_repo_that_tracks_it():
    data = Path("/d/Data")
    assert unversioned_blocks(Path("/d/Data/.datacore/modules/m"), data, ignored=False) is True
    assert unversioned_blocks(Path("/d/Data/.datacore/modules/m"), data, ignored=True) is False
    assert unversioned_blocks(data, data, ignored=True) is False     # gitignored in the Data repo
    assert unversioned_blocks(None, data, ignored=False) is False    # .claude/ behind a symlink, or gone


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


def _sh(cmd: str, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, env={"HOME": str(home), "PATH": "/usr/bin:/bin"})


def test_remote_preview_command_follows_symlinks_only_inside_data(tmp_path):
    data = tmp_path / "Data"
    data.mkdir()
    (data / "note one.md").write_text("ok")
    (tmp_path / "secret.json").write_text("SECRET")
    (data / "leak.md").symlink_to(tmp_path / "secret.json")
    (data / "env.md").symlink_to(data / "note one.md")
    r = _sh(remote_preview_command(str(data / "note one.md")), tmp_path)
    assert r.returncode == 0 and r.stdout == "ok"
    assert _sh(remote_preview_command(str(data / "env.md")), tmp_path).stdout == "ok"
    leak = _sh(remote_preview_command(str(data / "leak.md")), tmp_path)
    assert leak.returncode != 0 and "SECRET" not in leak.stdout


def test_remote_preview_command_refuses_a_non_preview_target_inside_data(tmp_path):
    data = tmp_path / "Data"
    data.mkdir()
    (data / ".env").write_text("KEY=1")
    (data / "x.md").symlink_to(data / ".env")
    r = _sh(remote_preview_command(str(data / "x.md")), tmp_path)
    assert r.returncode != 0 and "KEY" not in r.stdout


def test_remote_preview_command_accepts_uppercase_suffix(tmp_path):
    data = tmp_path / "Data"
    data.mkdir()
    (data / "Shot.PNG").write_text("img")
    assert _sh(remote_preview_command(str(data / "Shot.PNG")), tmp_path).stdout == "img"


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


@pytest.mark.parametrize("name,ok", [
    ("plur-new", True), ("dev_2.x", True), ("my work", False), ("x;touch y", False),
    ("../x", False), (".hidden", False), ("", False),
])
def test_safe_group(name, ok):
    assert safe_group(name) is ok


def test_title_sequence():
    assert title_sequence("plur-new") == "\033]0;☀️ ds: plur-new\007"
    assert title_sequence(None) == "\033]0;\007"


def test_permission_args_refuses_to_drop_flags_that_restrict_the_session():
    # Leaving --settings (deny rules) or --disallowedTools behind would loosen the session.
    for argv in (["claude", "--settings", "x.json"], ["claude", "--disallowedTools=Bash"]):
        with pytest.raises(ValueError):
            permission_args(argv, None)
