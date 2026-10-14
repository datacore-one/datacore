"""`creds add` reassembles the env itself, so the new value is served at once.

Before 2026-10-14 `add` committed the value and printed "Next: run 'creds sync'".
Agents skipped that line, and the owner's first `creds get` failed on a credential
that had just been added. The sync is now part of `add`.
"""
import subprocess

import pytest
import yaml

from creds import CredentialManager


def _install(tmp_path, sync_body):
    root = tmp_path / ".datacore/secrets"
    (root / "scripts").mkdir(parents=True)
    (root / "global.env").write_text("OLD=keep\n")
    (root / "credential-index.yaml").write_text(yaml.safe_dump({"credentials": []}))
    if sync_body is not None:
        sync = root / "scripts/sync.sh"
        sync.write_text("#!/bin/bash\n" + sync_body)
        sync.chmod(0o755)

    def git(*args):
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
                              check=True, capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "audit@example.invalid")
    git("config", "user.name", "Synthetic audit")
    git("add", ".")
    git("commit", "-qm", "baseline")
    return root


def _add(tmp_path):
    return CredentialManager(str(tmp_path)).cmd_add(
        "new", var_name="NEW_KEY", value="synthetic", scope="global", tier="medium",
        category="test", provider="test", description="synthetic test credential")


def test_add_runs_sync_after_commit(tmp_path, capsys):
    root = _install(tmp_path, 'grep -q "^NEW_KEY=" global.env && touch synced\n')
    assert _add(tmp_path) == 0
    assert (root / "synced").exists(), "add did not run sync.sh after writing the value"
    assert "Next: run 'creds sync'" not in capsys.readouterr().out


def test_failed_sync_is_reported_not_hidden(tmp_path, capsys):
    root = _install(tmp_path, "exit 3\n")
    assert _add(tmp_path) == 1
    out = capsys.readouterr()
    assert "creds sync" in out.out + out.err
    # The credential itself is stored and committed regardless.
    assert "NEW_KEY=synthetic" in (root / "global.env").read_text()


def test_missing_sync_script_keeps_the_old_hint(tmp_path, capsys):
    _install(tmp_path, None)
    assert _add(tmp_path) == 0
    assert "creds sync" in capsys.readouterr().out
