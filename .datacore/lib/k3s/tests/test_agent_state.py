"""Tests for bin/agent-state: back up what git does not hold, restore it anywhere.

An agent's home is mostly git checkouts, which origin already holds. The backup
carries everything else in the home except regenerable caches, plus a manifest of every
checkout (remote, branch, commit) and a git bundle of any commits origin does
not have yet, so a restore on a fresh machine clones and ends at the same
commit. Inside a checkout, files git does not track (ignored config such as
.datacore/env, untracked notes) travel too, and uncommitted edits travel as a
patch -- nothing is stashed or committed for the agent; caches are skipped.
"""
from __future__ import annotations

import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "bin" / "agent-state"


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def commit(repo, name, text):
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-qm", name)


@pytest.fixture
def home(tmp_path):
    """An agent home: state, one pushed checkout, one with an unpushed commit."""
    remotes = tmp_path / "remotes"
    h = tmp_path / "home"
    (h / ".hermes/sessions").mkdir(parents=True)
    (h / ".hermes/state.db").write_bytes(b"sqlite" * 100)
    (h / ".hermes/sessions/s1.jsonl").write_text("{}\n")
    (h / ".datacore/state").mkdir(parents=True)
    (h / ".datacore/state/seq.mark").write_text("41\n")
    (h / ".cache").mkdir()
    (h / ".cache/big.bin").write_bytes(b"x" * 1000)
    (h / "tris-sync.sh").write_text("#!/bin/sh\n")          # a script only in the home
    (h / ".ssh").mkdir(mode=0o700)
    (h / ".ssh/config").write_text("Host *\n")
    (h / ".hermes/hermes-agent").mkdir()
    (h / ".hermes/hermes-agent/code.py").write_text("x")     # runtime install, in the image
    for name in ("data", "runner"):
        bare = remotes / f"{name}.git"
        bare.mkdir(parents=True)
        git(bare, "init", "-q", "--bare", "-b", "main")
        work = h / ("Data" if name == "data" else ".datacore/v2-runner")
        work.mkdir(parents=True)
        git(work, "init", "-q", "-b", "main")
        git(work, "remote", "add", "origin", str(bare))
        commit(work, "README", name)
        git(work, "push", "-q", "-u", "origin", "main")
    commit(h / "Data", "local.txt", "only here")          # unpushed
    commit(h / "Data", ".gitignore", "secret.env\nnode_modules/\n")
    git(h / "Data", "push", "-q")
    commit(h / "Data", "local.txt", "only here, again")   # unpushed
    (h / "Data/README").write_text("edited, not committed")  # dirty
    (h / "Data/secret.env").write_text("TOKEN=ignored-by-git")  # ignored, only on disk
    (h / "Data/notes.md").write_text("untracked")
    (h / "Data/node_modules/x").mkdir(parents=True)
    (h / "Data/node_modules/x/i.js").write_text("cache")
    return h


def run(*args, env=None):
    return subprocess.run(["python3", str(TOOL), *args], capture_output=True, text=True,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", **(env or {})})


def test_backup_holds_state_and_a_manifest_not_the_checkouts(home, tmp_path):
    out = tmp_path / "b.tar.gz"
    r = run("backup", "--home", str(home), "--exclude", ".cache,.hermes/hermes-agent", "--out", str(out))
    assert r.returncode == 0, r.stderr
    names = tarfile.open(out).getnames()
    assert "state/.hermes/state.db" in names and "state/.datacore/state/seq.mark" in names
    assert "state/tris-sync.sh" in names, "anything outside a checkout is state"
    tracked = {"state/Data/README", "state/Data/local.txt", "state/Data/.gitignore", "state/.datacore/v2-runner/README"}
    assert not tracked & set(names), "tracked files come from git, not the backup"
    assert not any(".cache" in n or "hermes-agent" in n for n in names), names
    manifest = json.load(tarfile.open(out).extractfile("manifest.json"))
    repos = {r["path"]: r for r in manifest["repos"]}
    assert set(repos) == {"Data", ".datacore/v2-runner"}
    assert repos["Data"]["ahead"] == 1
    assert "state/Data/secret.env" in names and "state/Data/notes.md" in names
    assert not any("node_modules" in n for n in names)
    assert "patches/Data.patch" in names
    assert repos[".datacore/v2-runner"]["ahead"] == 0
    assert "bundles/Data.bundle" in names, "an unpushed commit must travel as a bundle"
    assert "uncommitted" in r.stderr and "Data" in r.stderr, "uncommitted work is reported"


def test_remote_urls_never_carry_credentials(home, tmp_path):
    git(home / "Data", "remote", "set-url", "origin", "https://user:SECRET@example.invalid/x.git")
    out = tmp_path / "b.tar.gz"
    run("backup", "--home", str(home), "--out", str(out), "--no-bundles")
    raw = tarfile.open(out).extractfile("manifest.json").read().decode()
    assert "SECRET" not in raw and "https://example.invalid/x.git" in raw


def test_restore_on_an_empty_home_ends_at_the_same_commits(home, tmp_path):
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    r = run("restore", "--home", str(fresh), "--file", str(out))
    assert r.returncode == 0, r.stderr
    assert (fresh / ".hermes/state.db").read_bytes() == (home / ".hermes/state.db").read_bytes()
    assert (fresh / ".datacore/state/seq.mark").read_text() == "41\n"
    assert (fresh / ".ssh").stat().st_mode & 0o777 == 0o700, "private directories keep their mode"
    for path in ("Data", ".datacore/v2-runner"):
        assert git(fresh / path, "rev-parse", "HEAD") == git(home / path, "rev-parse", "HEAD"), path
    assert (fresh / "Data/local.txt").read_text() == "only here, again"
    assert (fresh / "Data/secret.env").read_text() == "TOKEN=ignored-by-git"
    assert (fresh / "Data/notes.md").read_text() == "untracked"
    assert (fresh / "Data/README").read_text() == "edited, not committed"


def test_restore_never_overwrites_an_existing_home(home, tmp_path):
    out = tmp_path / "b.tar.gz"
    run("backup", "--home", str(home), "--out", str(out))
    r = run("restore", "--home", str(home), "--file", str(out))
    assert r.returncode != 0 and "not empty" in r.stderr


def test_an_excluded_path_is_neither_backed_up_nor_cloned(home, tmp_path):
    # A second checkout of the same repo (Data's OpenClaw workspace copy) is
    # dropped on purpose: excluding it must keep it out of the manifest too.
    dup = home / ".openclaw/workspace/Data"
    dup.parent.mkdir(parents=True)
    git(tmp_path, "clone", "-q", str(tmp_path / "remotes/data.git"), str(dup))
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--exclude", ".openclaw/workspace/Data",
               "--out", str(out)).returncode == 0
    manifest = json.load(tarfile.open(out).extractfile("manifest.json"))
    assert ".openclaw/workspace/Data" not in {r["path"] for r in manifest["repos"]}
    assert not any("workspace/Data" in n for n in tarfile.open(out).getnames())


def test_local_branches_no_remote_has_survive_a_restore(home, tmp_path):
    # Found on Data's host: module repos with a local branch of 15-24 commits
    # never pushed. A fresh clone gets only what origin has.
    repo = home / "Data"
    git(repo, "checkout", "-q", "-b", "pre-publish")
    commit(repo, "draft.md", "local branch work")
    git(repo, "checkout", "-q", "main")
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    r = run("restore", "--home", str(fresh), "--file", str(out))
    assert r.returncode == 0, r.stderr
    assert git(fresh / "Data", "rev-parse", "pre-publish") == git(repo, "rev-parse", "pre-publish")
    assert git(fresh / "Data", "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_a_repo_without_a_remote_travels_whole_with_its_history(home, tmp_path):
    # Data's OpenClaw workspace is a local-only repo; nothing can clone it.
    ws = home / ".openclaw/workspace"
    ws.mkdir(parents=True)
    git(ws, "init", "-q", "-b", "master")
    commit(ws, "SOUL.md", "who I am")
    commit(ws, "MEMORY.md", "what I remember")
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    r = run("restore", "--home", str(fresh), "--file", str(out))
    assert r.returncode == 0, r.stderr
    assert (fresh / ".openclaw/workspace/SOUL.md").read_text() == "who I am"
    assert git(fresh / ".openclaw/workspace", "rev-parse", "HEAD") == git(ws, "rev-parse", "HEAD")


def test_a_bundle_without_head_restores_its_branches(home, tmp_path):
    # Data's 2-plur-space: the checked-out branch is pushed, a side branch is
    # not -- the bundle then has no HEAD, and fetching HEAD must not be tried.
    repo = home / ".datacore/v2-runner"
    git(repo, "checkout", "-q", "-b", "backup/side")
    commit(repo, "side.md", "only on a side branch")
    git(repo, "checkout", "-q", "main")
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    r = run("restore", "--home", str(fresh), "--file", str(out))
    assert r.returncode == 0, r.stderr
    assert git(fresh / ".datacore/v2-runner", "rev-parse", "backup/side") == git(repo, "rev-parse", "backup/side")
