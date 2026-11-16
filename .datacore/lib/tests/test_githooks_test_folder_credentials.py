"""A repository's tests may hold credential-shaped test inputs, wherever its test folder sits.

WHY. The credential-pattern rule exempts test paths: the tests of a secret
detector have to contain strings shaped like secrets. The exemption matched a
test folder only when another folder came before it (packages/x/test/...), so a
repository that keeps its tests in a top-level test/ folder could not commit
them. On 2026-11-15 a merge was refused over three unchanged test files of a
secret detector for that reason. Everything outside a test folder is refused as
before, and so is a folder whose name only starts with "test".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_githooks_credential_store import FAKE, _commit, _install  # noqa: E402


def _commit_at(tmp_path, name):
    root = _install(tmp_path)
    repo = tmp_path / "some-product"
    (repo / name).parent.mkdir(parents=True)
    return _commit(repo, root, name, FAKE)


@pytest.mark.parametrize("folder", ["test", "tests", "__tests__", "testdata", "fixtures"])
def test_a_top_level_test_folder_commits_a_credential_shaped_input(tmp_path, folder):
    r = _commit_at(tmp_path, f"{folder}/mcp/secret-detector.test.ts")
    assert r.returncode == 0, (r.stdout + r.stderr)[-400:]


def test_a_nested_test_folder_still_commits_one(tmp_path):
    r = _commit_at(tmp_path, "packages/core/test/secret-detector.test.ts")
    assert r.returncode == 0, (r.stdout + r.stderr)[-400:]


def test_product_code_is_still_refused(tmp_path):
    r = _commit_at(tmp_path, "src/mcp/secret-detector.ts")
    assert r.returncode != 0 and "credential pattern" in (r.stdout + r.stderr)


@pytest.mark.parametrize("name", ["testbed/keys.env", "contest/keys.env", "test.env"])
def test_a_name_that_only_looks_like_a_test_folder_is_still_refused(tmp_path, name):
    r = _commit_at(tmp_path, name)
    assert r.returncode != 0 and "credential pattern" in (r.stdout + r.stderr)
