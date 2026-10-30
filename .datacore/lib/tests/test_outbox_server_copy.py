"""The archiver copies to the server over plain ssh and confirms the copy before it may delete.

The first real run on 2026-10-18 failed on all 92 files: macOS now ships
openrsync, which rejects `-s`. Nothing was lost only because the copy failed
before the delete. The local file is deleted once archive_item returns True, so
True must mean "the server holds these exact bytes", checked by sha256, not
"the copy command exited 0".

The ssh hop is replaced by a local shell whose HOME is a temp dir, so the remote
side runs real shell commands against a real filesystem.
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".datacore" / "modules" / "outbox" / "lib"))

import archive_sync as A  # noqa: E402


class _Config:
    server_host = "archive-host"
    archive_repos = {"1-a": "Data/1-a-archive"}


def _fake_remote(monkeypatch, remote_home, corrupt=False):
    real_run = subprocess.run

    def run(argv, *args, **kwargs):
        if argv[:1] == ["ssh"]:
            cmd = argv[-1]
            if corrupt:
                cmd = cmd.replace("cat >", "head -c 3 >", 1)
            return real_run(["bash", "-c", cmd], *args, env={"HOME": str(remote_home), "PATH": "/usr/bin:/bin"}, **kwargs)
        raise AssertionError(f"unexpected command: {argv[:2]}")

    monkeypatch.setattr(A.subprocess, "run", run)


def _item(tmp_path, name, data):
    src = tmp_path / "local" / "1-a" / "4-archive" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(data)
    return A.ArchiveItem(source_path=src, space="1-a", relative_path=name)


def test_a_copy_with_awkward_names_lands_byte_for_byte(tmp_path, monkeypatch):
    remote = tmp_path / "remote"
    remote.mkdir()
    _fake_remote(monkeypatch, remote)
    data = b"report body\n" * 1000
    item = _item(tmp_path, "notes/a page\nwith newline 'and' quotes.md", data)
    ok, msg = A.ServerArchiver(_Config()).archive_item(item)
    assert ok, msg
    landed = remote / "Data/1-a-archive" / item.relative_path
    assert hashlib.sha256(landed.read_bytes()).hexdigest() == hashlib.sha256(data).hexdigest()
    assert item.source_path.exists(), "archive_item copies; deleting is the caller's step"


def test_a_copy_that_does_not_match_is_not_success(tmp_path, monkeypatch):
    remote = tmp_path / "remote"
    remote.mkdir()
    _fake_remote(monkeypatch, remote, corrupt=True)
    item = _item(tmp_path, "doc.md", b"0123456789")
    ok, msg = A.ServerArchiver(_Config()).archive_item(item)
    assert not ok, "a truncated copy must never let the caller delete the original"
    assert "checksum" in msg


def test_an_archive_repo_ignores_private_context_layers(monkeypatch, tmp_path):
    """SPC-3: every repository on a machine ignores CLAUDE.local.md, archive repos included.
    The archives on the executor host were created by a bare `git init` and ignored nothing."""
    remote_home = tmp_path / "home"
    remote_home.mkdir()
    real_run = subprocess.run
    _fake_remote(monkeypatch, remote_home)
    arch = A.ServerArchiver(_Config())
    existing = remote_home / "Data" / "1-a-archive"
    for repo_exists in (False, True):
        if repo_exists:  # an archive created before this rule: still gets the ignore line, once
            (existing / ".gitignore").write_text("*.tmp\n")
        assert arch.ensure_repo_exists("1-a")
        assert arch.ensure_repo_exists("1-a")
        r = real_run(["git", "-C", str(existing), "check-ignore", "-q", "CLAUDE.local.md"])
        assert r.returncode == 0
        assert (existing / ".gitignore").read_text().count("CLAUDE.local.md") == 1


def test_a_local_archive_repo_ignores_private_context_layers(tmp_path):
    cfg = type("C", (), {"local_archive_path": tmp_path})()
    arch = A.LocalArchiver(cfg)
    assert arch.ensure_repo_exists("1-a") and arch.ensure_repo_exists("1-a")
    repo = tmp_path / "1-a-archive"
    assert subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", "CLAUDE.local.md"]).returncode == 0
    assert (repo / ".gitignore").read_text().count("CLAUDE.local.md") == 1
