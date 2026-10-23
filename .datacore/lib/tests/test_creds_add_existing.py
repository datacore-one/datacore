"""`creds add --existing` indexes a variable already in its store, without its value.

2026-10-23: the PLUR X keys (PLUR_X_*) sat in spaces/5-plur.env, where
release.sh reads them, but were in no index row, so `creds get` refused them.
`creds add` could not fix that: it asks for the value and refuses a variable
that already exists in the target store. Retyping a live key to index it is the
wrong trade, so `--existing` adds the index row and leaves the store untouched.
"""
import subprocess

import pytest
import yaml

from creds import CredentialManager

STORED = "stored-placeholder"


def _install(tmp_path, env_text=f"PLUR_X_API_KEY={SECRET}\n"):
    root = tmp_path / ".datacore/secrets"
    (root / "spaces").mkdir(parents=True)
    (root / "spaces/demo.env").write_text(env_text)
    (root / "global.env").write_text("")
    (root / "credential-index.yaml").write_text(yaml.safe_dump({"credentials": []}))

    def git(*args):
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
                              check=True, capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "audit@example.invalid")
    git("config", "user.name", "Synthetic audit")
    git("add", ".")
    git("commit", "-qm", "baseline")
    return root, git


def _add(tmp_path, **kw):
    args = dict(var_name="PLUR_X_API_KEY", scope="space", space="demo", tier="high",
                category="social", provider="x-twitter", description="synthetic", existing=True)
    args.update(kw)
    return CredentialManager(str(tmp_path)).cmd_add("plur-x-api-key", **args)


def _rows(root):
    return yaml.safe_load((root / "credential-index.yaml").read_text())["credentials"]


def test_indexes_a_variable_already_in_the_store_without_touching_it(tmp_path, capsys, monkeypatch):
    root, git = _install(tmp_path)
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: pytest.fail("--existing must not ask for a value"))
    before = (root / "spaces/demo.env").read_bytes()
    assert _add(tmp_path) == 0
    assert (root / "spaces/demo.env").read_bytes() == before
    rows = _rows(root)
    assert [(r["id"], r["var_name"], r["scope"], r["space"]) for r in rows] == [
        ("plur-x-api-key", "PLUR_X_API_KEY", "space", "demo")]
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err
    assert "credential-index.yaml" in git("show", "--name-only", "--format=", "HEAD").stdout


def test_refuses_when_the_variable_is_not_in_the_store(tmp_path):
    root, _ = _install(tmp_path, env_text="OTHER=1\n")
    assert _add(tmp_path) == 1
    assert _rows(root) == []


def test_refuses_a_value_with_existing(tmp_path):
    root, _ = _install(tmp_path)
    assert _add(tmp_path, value="something") == 1
    assert _rows(root) == []
    assert (root / "spaces/demo.env").read_text() == f"PLUR_X_API_KEY={SECRET}\n"


def test_refuses_a_variable_that_is_already_indexed(tmp_path):
    root, _ = _install(tmp_path)
    assert _add(tmp_path) == 0
    assert _add(tmp_path) == 1
    assert len(_rows(root)) == 1


def test_export_form_in_the_store_counts(tmp_path):
    root, _ = _install(tmp_path, env_text=f"export PLUR_X_API_KEY={SECRET}\n")
    assert _add(tmp_path) == 0
    assert len(_rows(root)) == 1


def test_cli_flag(tmp_path, monkeypatch):
    import creds
    root, _ = _install(tmp_path)
    monkeypatch.setattr("sys.argv", ["creds", "--data-dir", str(tmp_path), "add", "plur-x-api-key",
                                     "--var", "PLUR_X_API_KEY", "--scope", "space", "--space", "demo",
                                     "--tier", "high", "--category", "social", "--provider", "x-twitter",
                                     "--description", "synthetic", "--existing"])
    assert creds.main() in (0, None)
    assert len(_rows(root)) == 1
