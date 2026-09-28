"""MEM-63: the github module keeps no user data inside itself.

The repo list and the scan cache live in the selected space's private
module-data folder, resolved through the core module_context helper.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE / "lib"))
sys.path.insert(0, str(MODULE.parents[1] / "lib"))

import github_paths  # noqa: E402


@pytest.fixture
def install(tmp_path, monkeypatch):
    root = tmp_path / "Data"
    space = root / "0-personal"
    (space / ".datacore").mkdir(parents=True)
    (space / ".datacore/config.yaml").write_text("space: {name: personal, type: personal}\n")
    other = root / "9-practice/.datacore"
    other.mkdir(parents=True)
    (other / "config.yaml").write_text("space: {name: practice, type: personal}\n")
    monkeypatch.setenv("DATACORE_ROOT", str(root))
    monkeypatch.delenv("DATACORE_SPACE", raising=False)
    return root, space


def test_data_resolves_to_the_private_space_folder(install):
    _, space = install
    data = github_paths.data_dir()
    # A private folder of the space (module-data/, or the space's own scoped
    # modules/ folder on a fresh install) -- never the installed module.
    assert data.is_relative_to(space / ".datacore") and not data.is_relative_to(MODULE)
    assert data.name == "data" and data.parent.name == "github"
    assert data.stat().st_mode & 0o077 == 0


def test_the_shell_entry_prints_the_same_folder(install):
    root, space = install
    out = subprocess.run([sys.executable, str(MODULE / "lib/github_paths.py")], capture_output=True,
                         text=True, env={**os.environ, "DATACORE_ROOT": str(root)}, check=True).stdout.strip()
    assert out == str(github_paths.data_dir())


def test_repo_discovery_caches_into_the_private_folder(install, monkeypatch):
    import repo_discovery
    root, space = install
    seen = {}
    monkeypatch.setattr(repo_discovery, "discover_repos", lambda data_dir, cache_path, **kw: seen.update(cache=cache_path) or {})
    monkeypatch.setattr(sys, "argv", ["repo_discovery.py", "--data-dir", str(root)])
    repo_discovery.main()
    assert seen["cache"] == github_paths.data_dir() / "repos.json"
    assert seen["cache"].is_relative_to(space)


def test_triage_script_uses_the_resolver_not_the_module_folder():
    script = (MODULE / "lib/triage_github.sh").read_text()
    assert 'CACHE_DIR="$MODULE_DIR/data"' not in script
    assert "github_paths.py" in script
