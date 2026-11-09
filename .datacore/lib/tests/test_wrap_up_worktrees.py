"""A session removes the worktrees it made, and only those (board D12, 2026-10-01).

Worktrees outlive the sessions that create them: the disk had ~5 GB free and
scratch checkouts from finished sessions were part of why. /wrap-up now removes
this session's own worktrees -- ones whose path carries its session id, or that
hold a file it wrote -- when they are clean and their HEAD is on a remote, with
`git worktree remove` and never --force. Anything else is reported as kept, with
the reason, and another session's worktree is never touched.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wrap_up_mechanics as wm  # noqa: E402

SID = "sess-1234"


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def _fleet(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    root = tmp_path / "Data"
    _git(tmp_path, "init", "-q", "-b", "main", str(root))
    for k, v in (("user.email", "t@example.invalid"), ("user.name", "t"), ("commit.gpgsign", "false")):
        _git(root, "config", k, v)
    (root / "a.txt").write_text("a\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "a")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-q", "-u", "origin", "main")
    scratch = tmp_path / "scratch" / SID
    scratch.mkdir(parents=True)
    trees = {
        "pushed": scratch / "pushed",                 # mine, clean, on the remote -> removed
        "unpushed": scratch / "unpushed",             # mine, a commit nobody else has -> kept
        "dirty": scratch / "dirty",                   # mine, uncommitted change -> kept
        "other": tmp_path / "scratch" / "other-session" / "wt",  # not mine -> left alone
    }
    for name, path in trees.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(root, "worktree", "add", "-q", "--detach", str(path), "main")
    (trees["unpushed"] / "b.txt").write_text("b\n")
    _git(trees["unpushed"], "add", "b.txt")
    _git(trees["unpushed"], "commit", "-q", "-m", "b")
    (trees["dirty"] / "a.txt").write_text("changed\n")
    monkeypatch.setattr(wm, "DATACORE_ROOT", root)
    monkeypatch.setattr(wm, "spaces", lambda: [])
    monkeypatch.setattr(wm, "session_key", lambda: SID)
    monkeypatch.setattr(wm, "session_files", lambda: ([], None))
    return root, trees


def test_only_this_sessions_clean_pushed_worktree_is_removed(tmp_path, monkeypatch):
    root, trees = _fleet(tmp_path, monkeypatch)
    out = wm.cmd_worktrees(dry_run=False)
    removed = {Path(r["path"]).resolve() for r in out["removed"]}
    kept = {Path(r["path"]).resolve(): r["reason"] for r in out["kept"]}
    assert removed == {trees["pushed"].resolve()}
    assert not trees["pushed"].exists()
    assert "not on any remote" in kept[trees["unpushed"].resolve()]
    assert "uncommitted" in kept[trees["dirty"].resolve()]
    assert trees["unpushed"].exists() and trees["dirty"].exists()
    assert trees["other"].exists(), "another session's worktree was touched"
    assert trees["other"].resolve() not in kept and trees["other"].resolve() not in removed
    assert any(Path(r["path"]).resolve() == trees["other"].resolve() for r in out["others"])


def test_dry_run_removes_nothing(tmp_path, monkeypatch):
    root, trees = _fleet(tmp_path, monkeypatch)
    out = wm.cmd_worktrees(dry_run=True)
    assert [Path(r["path"]).resolve() for r in out["removed"]] == [trees["pushed"].resolve()]
    assert trees["pushed"].exists()


def test_a_worktree_holding_a_file_this_session_wrote_is_its_own(tmp_path, monkeypatch):
    root, trees = _fleet(tmp_path, monkeypatch)
    monkeypatch.setattr(wm, "session_files", lambda: ([str(trees["other"] / "a.txt")], None))
    out = wm.cmd_worktrees(dry_run=True)
    assert trees["other"].resolve() in {Path(r["path"]).resolve() for r in out["removed"]}


def test_never_forced():
    src = (Path(wm.__file__)).read_text()
    body = src[src.index("def cmd_worktrees"):]
    body = body[:body.index("\ndef ", 1)]
    assert "--force" not in body and '"-f"' not in body


def test_the_audit_fails_while_this_sessions_worktree_is_left(tmp_path, monkeypatch):
    root, trees = _fleet(tmp_path, monkeypatch)
    rows = {c["check"]: c for c in wm.cmd_audit()["checks"]}
    assert rows["session worktrees removed"]["status"] == "fail"
    assert "pushed" in rows["session worktrees removed"]["detail"]
