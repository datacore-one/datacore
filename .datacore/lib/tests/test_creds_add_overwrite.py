"""`creds add <id> --overwrite` replaces the value of a credential already indexed.

2026-10-26: the Datafund X access token was regenerated with write permission.
`creds add` refused it ("already indexed") and saved nothing, so the owner's sync
reassembled the old, now-revoked token. `--overwrite` asks for the new value
(hidden prompt), takes the store location from the existing index row, replaces
that one line, leaves the index alone and commits the store.
"""
import subprocess

import pytest
import yaml

from creds import CredentialManager

OLD, NEW = "old-placeholder-value", "new-placeholder-value"
ROW = {"id": "demo-x-access-token", "name": "Demo X token", "type": "api_key", "tier": "high",
       "scope": "project", "project": "demo-x", "category": "social", "provider": "x-twitter",
       "var_name": "DEMO_X_ACCESS_TOKEN", "description": "synthetic"}


def _install(tmp_path, rows=(ROW,), env_text=f"OTHER=1\nDEMO_X_ACCESS_TOKEN={OLD}\nAFTER=2\n"):
    root = tmp_path / ".datacore/secrets"
    (root / "projects").mkdir(parents=True)
    (root / "projects/demo-x.env").write_text(env_text)
    (root / "global.env").write_text("")
    (root / "credential-index.yaml").write_text(yaml.safe_dump({"credentials": list(rows)}, sort_keys=False))

    def git(*args):
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
                              check=True, capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "audit@example.invalid")
    git("config", "user.name", "Synthetic audit")
    git("add", ".")
    git("commit", "-qm", "baseline")
    return root, git


def test_replaces_the_value_in_the_indexed_store(tmp_path, capsys, monkeypatch):
    root, git = _install(tmp_path)
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: NEW)
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("--overwrite must not ask for metadata"))
    index_before = (root / "credential-index.yaml").read_bytes()
    assert CredentialManager(str(tmp_path)).cmd_add("demo-x-access-token", overwrite=True) == 0
    assert (root / "projects/demo-x.env").read_text() == f"OTHER=1\nDEMO_X_ACCESS_TOKEN={NEW}\nAFTER=2\n"
    assert (root / "credential-index.yaml").read_bytes() == index_before
    out = capsys.readouterr()
    assert NEW not in out.out + out.err and OLD not in out.out + out.err
    assert "projects/demo-x.env" in git("show", "--name-only", "--format=", "HEAD").stdout


def test_refuses_a_credential_that_is_not_indexed(tmp_path, monkeypatch):
    root, _ = _install(tmp_path, rows=())
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: NEW)
    assert CredentialManager(str(tmp_path)).cmd_add("demo-x-access-token", overwrite=True) == 1
    assert OLD in (root / "projects/demo-x.env").read_text()


def test_refuses_when_the_variable_is_missing_from_the_store(tmp_path, monkeypatch):
    root, _ = _install(tmp_path, env_text="OTHER=1\n")
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: NEW)
    assert CredentialManager(str(tmp_path)).cmd_add("demo-x-access-token", overwrite=True) == 1
    assert (root / "projects/demo-x.env").read_text() == "OTHER=1\n"


def test_refuses_overwrite_with_existing(tmp_path):
    root, _ = _install(tmp_path)
    assert CredentialManager(str(tmp_path)).cmd_add("demo-x-access-token", overwrite=True, existing=True) == 1
    assert OLD in (root / "projects/demo-x.env").read_text()


def test_cli_flag(tmp_path, monkeypatch):
    import creds
    root, _ = _install(tmp_path)
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: NEW)
    monkeypatch.setattr("sys.argv", ["creds", "--data-dir", str(tmp_path), "add", "demo-x-access-token", "--overwrite"])
    assert creds.main() in (0, None)
    assert f"DEMO_X_ACCESS_TOKEN={NEW}" in (root / "projects/demo-x.env").read_text()
