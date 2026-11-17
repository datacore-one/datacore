"""A push the archive's remote rejects makes the sync report failure.

commit_changes ended its push with `|| true`, so a remote that refused every
push still gave status "success" and exit 0: the archive could stay stuck on
the server, never reaching its remote, and every run looked healthy.

The ssh hop is replaced by a local shell whose HOME is a temp dir (as in
test_outbox_server_copy.py), and the archive repo's origin is a real bare repo.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".datacore" / "modules" / "outbox" / "lib"))

import archive_sync as A  # noqa: E402

REPO = "Data/1-a-archive"


def _setup(tmp_path, monkeypatch, origin):
    """origin: None (no remote), "accept", or "reject" (pre-receive exits 1)."""
    remote_home = tmp_path / "remote"
    remote_home.mkdir()
    (remote_home / ".gitconfig").write_text("[user]\n\tname = t\n\temail = t@example.com\n")
    env = {"HOME": str(remote_home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "GIT_CONFIG_NOSYSTEM": "1"}
    git = lambda *a: subprocess.run(["git", *a], env=env, check=True, capture_output=True)  # noqa: E731

    repo = remote_home / REPO
    repo.mkdir(parents=True)
    git("init", "-q", str(repo))
    if origin:
        bare = tmp_path / "origin.git"
        git("init", "-q", "--bare", str(bare))
        if origin == "reject":
            hook = bare / "hooks" / "pre-receive"
            hook.write_text("#!/bin/sh\necho 'archive remote refuses pushes' >&2\nexit 1\n")
            hook.chmod(0o755)
        git("-C", str(repo), "remote", "add", "origin", str(bare))

    data_root = tmp_path / "Data"
    (data_root / ".datacore").mkdir(parents=True)
    (data_root / ".datacore" / "settings.yaml").write_text(
        "outbox:\n  archive_location: server\n  server_host: archive-host\n"
        f"  archive_repos:\n    1-a: {REPO}\n"
    )
    queue = data_root / "1-a" / "4-archive"
    queue.mkdir(parents=True)
    (queue / "doc.md").write_text("finished work\n")

    real_run = subprocess.run

    def run(argv, *args, **kwargs):
        if argv[:1] == ["ssh"]:
            return real_run(["bash", "-c", argv[-1]], *args, env=env, **kwargs)
        raise AssertionError(f"unexpected command: {argv[:2]}")

    monkeypatch.setattr(A.subprocess, "run", run)
    return data_root


def _run_cli(data_root, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["archive_sync.py", "--json", "--data-root", str(data_root)])
    with pytest.raises(SystemExit) as exc:
        A.main()
    return exc.value.code, json.loads(capsys.readouterr().out)


def test_a_rejected_push_is_reported_as_failure(tmp_path, monkeypatch, capsys):
    data_root = _setup(tmp_path, monkeypatch, origin="reject")
    code, out = _run_cli(data_root, monkeypatch, capsys)
    assert code == 1, "a stuck archive must not exit 0"
    assert out["status"] == "error"
    assert any("push" in e.lower() for e in out["errors"]), out["errors"]


def test_an_accepted_push_is_success(tmp_path, monkeypatch, capsys):
    data_root = _setup(tmp_path, monkeypatch, origin="accept")
    code, out = _run_cli(data_root, monkeypatch, capsys)
    assert (code, out["status"], out["errors"]) == (0, "success", [])


def test_an_archive_with_no_remote_is_still_success(tmp_path, monkeypatch, capsys):
    data_root = _setup(tmp_path, monkeypatch, origin=None)
    code, out = _run_cli(data_root, monkeypatch, capsys)
    assert (code, out["status"], out["errors"]) == (0, "success", [])
