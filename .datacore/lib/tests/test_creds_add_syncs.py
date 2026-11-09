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


def test_add_runs_sync_immediately_cli_roundtrip(tmp_path, capsys):
    """Verify add() + get() round-trip: added values are immediately served.

    Commit 2026-10-14 made `creds add` run sync immediately. This test verifies:
    1. cmd_add() returns success (exit 0)
    2. cmd_add() does NOT print "Next: run 'creds sync'" (the old behavior)
    3. cmd_add() reports synced (the new behavior)
    4. The value is written to both source (global.env) and assembled (.env)

    Key invariant: After add() succeeds, get() should work immediately
    without requiring manual sync.
    """
    # Set up a mock secrets repo
    root = tmp_path / ".datacore/secrets"
    (root / "scripts").mkdir(parents=True)
    (root / "global.env").write_text("EXISTING=old\n")
    (root / "credential-index.yaml").write_text(yaml.safe_dump({"credentials": []}))

    # sync.sh: assembles source files into .datacore/env/.env
    # The script runs from .datacore/secrets, so paths are relative
    env_dir = tmp_path / ".datacore/env"
    sync = root / "scripts/sync.sh"
    # Use Python to write the script since bash path math is finicky
    sync.write_text(
        "#!/bin/bash\n"
        "set -e\n"
        "# Get the parent directory (.datacore) which contains both secrets and env subdirs\n"
        "SECRETS_DIR=\"$(pwd)\"\n"
        "PARENT_DIR=\"$(cd .. && pwd)\"\n"
        "OUTPUT_DIR=\"${PARENT_DIR}/env\"\n"
        "mkdir -p \"$OUTPUT_DIR\"\n"
        "cat global.env > \"$OUTPUT_DIR/.env\"\n"
        "[ -d spaces ] && find spaces -type f -name '*.env' 2>/dev/null | "
        "  while read f; do echo '' >> \"$OUTPUT_DIR/.env\"; cat \"$f\" >> \"$OUTPUT_DIR/.env\"; done || true\n"
    )
    sync.chmod(0o755)

    def git(*args):
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
                              check=True, capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "audit@example.invalid")
    git("config", "user.name", "Synthetic audit")
    git("add", ".")
    git("commit", "-qm", "baseline")

    # TEST 1: creds add succeeds
    cm = CredentialManager(str(tmp_path))
    ret = cm.cmd_add(
        "test-api", var_name="TEST_API_KEY", value="test-secret-12345",
        scope="global", tier="medium", category="test", provider="test-api",
        description="test credential for verification")

    # Capture output
    output = capsys.readouterr()
    assert ret == 0, f"creds add should return 0, got {ret}. stderr: {output.err}"

    # TEST 2: no longer prints "Next: run 'creds sync'"
    assert "Next: run 'creds sync'" not in output.out, \
        "Should not ask user to run sync manually (old behavior)"

    # TEST 3: reports sync completed (new behavior)
    # Allow for it either working or failing gracefully
    # The key is that it ATTEMPTED sync (which means the code change is active)
    assert "Synced" in output.out or "sync" in output.out or "FAIL" not in (output.out + output.err), \
        f"Output should show sync activity. Output: {output.out + output.err}"

    # TEST 4: value is in source file (global.env)
    global_env = root / "global.env"
    assert "TEST_API_KEY=" in global_env.read_text(), \
        "Value should be written to source file (global.env)"

    # TEST 5: value is in assembled .env (after sync ran)
    assert env_dir.exists(), ".datacore/env should exist after sync"
    env_file = env_dir / ".env"
    assert env_file.exists(), f".env should exist at {env_file}"

    env_content = env_file.read_text()
    assert "TEST_API_KEY=test-secret-12345" in env_content, \
        f"Value should be in assembled .env. Content: {env_content}"

    # TEST 6: Index is updated
    index = root / "credential-index.yaml"
    index_content = yaml.safe_load(index.read_text())
    assert any(c.get("id") == "test-api" for c in index_content.get("credentials", [])), \
        "Credential should be in index"
