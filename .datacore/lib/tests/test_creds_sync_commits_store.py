"""`creds sync` commits local edits to the secrets store and pushes them.

Before 2026-10-22 a value edited in a space file was assembled into this host's
env and went nowhere else, and `creds add` committed without pushing: the Mac's
store was 12 commits ahead of central. That is the July X-key failure shape
(a rotation that stays on one machine), so sync now carries the edit out.
"""
import subprocess

from creds import CredentialManager

MARKER = "synthetic-value-never-in-a-message"


def _git(root, *args):
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
                          check=True, capture_output=True, text=True).stdout


def _install(tmp_path, sync_body="exit 0\n"):
    origin = tmp_path / "central.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    root = tmp_path / ".datacore/secrets"
    for d in ("scripts", "spaces", "projects", "instances"):
        (root / d).mkdir(parents=True)
    (root / "global.env").write_text("OLD=keep\n")
    (root / "spaces/1-x.env").write_text("X_KEY=old\n")
    (root / "credential-index.yaml").write_text("credentials: []\n")
    (root / "instances/local.yaml").write_text("name: local\n")
    sync = root / "scripts/sync.sh"
    sync.write_text("#!/bin/bash\n" + sync_body)
    sync.chmod(0o755)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "audit@example.invalid")
    _git(root, "config", "user.name", "Synthetic audit")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "push", "-q", "-u", "origin", "main")
    return root, origin


def _sync(tmp_path):
    return CredentialManager(str(tmp_path)).cmd_sync()


def test_store_edits_are_committed_and_pushed(tmp_path, capsys):
    root, origin = _install(tmp_path)
    (root / "spaces/1-x.env").write_text(f"X_KEY={MARKER}\n")
    (root / "projects/p.env").write_text("P_KEY=v\n")
    (root / "instances/crt.yaml.pending").write_text("name: crt\n")
    assert _sync(tmp_path) == 0
    committed = _git(root, "show", "--name-only", "--format=%B", "HEAD")
    assert "spaces/1-x.env" in committed and "projects/p.env" in committed
    assert "crt.yaml.pending" not in committed, "a .pending manifest is not a store file"
    assert MARKER not in committed + capsys.readouterr().out
    assert _git(origin, "rev-parse", "main") == _git(root, "rev-parse", "HEAD")


def test_clean_tree_makes_no_commit(tmp_path):
    root, _ = _install(tmp_path)
    before = _git(root, "rev-parse", "HEAD")
    assert _sync(tmp_path) == 0
    assert _git(root, "rev-parse", "HEAD") == before


def test_earlier_unpushed_commits_are_pushed(tmp_path):
    root, origin = _install(tmp_path)
    (root / "global.env").write_text("OLD=keep\nNEW=1\n")
    _git(root, "commit", "-qam", "feat: add credential new")  # what `creds add` leaves
    assert _sync(tmp_path) == 0
    assert _git(origin, "rev-parse", "main") == _git(root, "rev-parse", "HEAD")


def test_failed_push_is_reported(tmp_path, capsys):
    root, _ = _install(tmp_path)
    _git(root, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    (root / "spaces/1-x.env").write_text("X_KEY=new\n")
    assert _sync(tmp_path) != 0
    out = capsys.readouterr()
    assert "push" in (out.out + out.err).lower()
