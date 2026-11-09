"""The credential store can commit credentials; every other repository still cannot.

WHY. `creds add` writes the value into .datacore/secrets/ (its own repository)
and commits it there. The global hooks treated that repository like any other
and refused the commit ("credential pattern -- move secrets to .datacore/env/"),
so every `creds add` since the hooks went global left the entry staged and
uncommitted: on 2026-09-30 three owner-added credentials (two GitHub tokens and
Data's bot) had never reached the central store, so no other machine could get
them. The credential-pattern rule is skipped for the store alone; its
conflict-marker check and the ledger gate still run.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
DATACORE = LIB.parent
FAKE = "GH_TOKEN_X=ghp_" + "A" * 36 + "\n"


def _install(tmp_path: Path) -> Path:
    root = tmp_path / "Data"
    (root / ".datacore").mkdir(parents=True)
    shutil.copytree(DATACORE / "githooks", root / ".datacore" / "githooks")
    shutil.copytree(DATACORE / "hooks", root / ".datacore" / "hooks")
    (root / ".datacore" / "lib").symlink_to(LIB)
    return root


def _commit(repo: Path, root: Path, name: str, text: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_INDEX_FILE", "NIGHTSHIFT_RUN")}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run([*g, "init", "-q", "-b", "main", str(repo)], check=True, env=env, capture_output=True)
    subprocess.run([*g, "config", "core.hooksPath", str(root / ".datacore" / "githooks")],
                   cwd=repo, check=True, env=env)
    (repo / name).write_text(text)
    subprocess.run([*g, "add", name], cwd=repo, check=True, env=env)
    return subprocess.run([*g, "commit", "-q", "-m", "x"], cwd=repo, env=env, capture_output=True, text=True)


def test_the_credential_store_commits_a_credential(tmp_path):
    root = _install(tmp_path)
    r = _commit(root / ".datacore" / "secrets", root, "global.env", FAKE)
    assert r.returncode == 0, (r.stdout + r.stderr)[-400:]


def test_any_other_repository_still_refuses_a_credential(tmp_path):
    root = _install(tmp_path)
    r = _commit(tmp_path / "some-repo", root, "notes.env", FAKE)
    assert r.returncode != 0 and "credential pattern" in (r.stdout + r.stderr)


def test_the_credential_store_still_refuses_conflict_markers(tmp_path):
    root = _install(tmp_path)
    r = _commit(root / ".datacore" / "secrets", root, "global.env",
                "<<<<<<< ours\nA=1\n=======\nA=2\n>>>>>>> theirs\n")
    assert r.returncode != 0 and "conflict markers" in (r.stdout + r.stderr)
