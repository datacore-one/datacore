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


def test_a_bundle_needs_only_what_origin_has_now(home, tmp_path):
    # Found on Data's comms module: the local origin/* refs held a commit that
    # origin no longer has (a branch deleted or rewritten upstream). A bundle cut
    # against the local refs then needs that commit, and a fresh clone lacks it.
    repo = home / "Data"
    bare = tmp_path / "remotes/data.git"
    git(repo, "remote", "set-url", "origin", f"file://{bare}")  # a real transport, not a local copy
    git(repo, "checkout", "-q", "-b", "gone")
    commit(repo, "gone.md", "was on origin once")
    git(repo, "push", "-q", "origin", "gone")
    commit(repo, "more.md", "local only, on top of it")
    git(bare, "branch", "-D", "gone")           # upstream deletes the branch
    git(bare, "reflog", "expire", "--expire=now", "--all")
    git(bare, "gc", "-q", "--prune=now")         # and the commit is gone from origin
    git(repo, "checkout", "-q", "main")          # local origin/gone still points at it
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    r = run("restore", "--home", str(fresh), "--file", str(out))
    assert r.returncode == 0, r.stderr
    assert git(fresh / "Data", "rev-parse", "gone") == git(repo, "rev-parse", "gone")


def test_resume_continues_a_partial_restore(home, tmp_path):
    ws = home / ".openclaw/workspace"               # a whole repo: read-only git objects
    ws.mkdir(parents=True)
    git(ws, "init", "-q", "-b", "master")
    commit(ws, "SOUL.md", "who I am")
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert run("restore", "--home", str(fresh), "--file", str(out)).returncode == 0
    r = run("restore", "--home", str(fresh), "--file", str(out), "--resume")
    assert r.returncode == 0, r.stderr
    assert git(fresh / "Data", "rev-parse", "HEAD") == git(home / "Data", "rev-parse", "HEAD")


def test_a_repo_whose_remote_cannot_be_read_travels_whole(home, tmp_path):
    # Data's slides module: its GitHub repo no longer exists, so his copy is the only one.
    repo = home / ".datacore/v2-runner"
    git(repo, "remote", "set-url", "origin", f"file://{tmp_path}/remotes/gone.git")
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    r = run("restore", "--home", str(fresh), "--file", str(out))
    assert r.returncode == 0, r.stderr
    assert git(fresh / ".datacore/v2-runner", "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")


def test_bundles_ignore_refs_a_clone_never_fetches(home, tmp_path):
    # GitHub lists refs/pull/*/head in ls-remote; a clone does not fetch them,
    # so a bundle cut against one needs a commit the clone lacks.
    repo = home / "Data"
    bare = tmp_path / "remotes/data.git"
    git(repo, "remote", "set-url", "origin", f"file://{bare}")
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "pr.md", "in a pull request")
    git(repo, "push", "-q", "origin", "feature:refs/pull/1/head")
    commit(repo, "more.md", "local only")
    git(repo, "checkout", "-q", "main")
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    r = run("restore", "--home", str(fresh), "--file", str(out))
    assert r.returncode == 0, r.stderr
    assert git(fresh / "Data", "rev-parse", "feature") == git(repo, "rev-parse", "feature")


def test_pushed_local_branches_come_back_as_local_branches(home, tmp_path):
    repo = home / "Data"
    git(repo, "checkout", "-q", "-b", "openclaw/adaptations")
    commit(repo, "adapt.md", "pushed work")
    git(repo, "push", "-q", "-u", "origin", "openclaw/adaptations")
    git(repo, "checkout", "-q", "main")
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert run("restore", "--home", str(fresh), "--file", str(out)).returncode == 0
    assert git(fresh / "Data", "rev-parse", "refs/heads/openclaw/adaptations") == git(repo, "rev-parse", "openclaw/adaptations")


def test_extra_remotes_come_back_with_their_branches(home, tmp_path):
    # Data's plur-public: a fork remote beside origin, with branches only there.
    repo = home / "Data"
    fork = tmp_path / "remotes/fork.git"
    fork.mkdir()
    git(fork, "init", "-q", "--bare", "-b", "main")
    git(repo, "remote", "add", "fork", f"file://{fork}")
    git(repo, "checkout", "-q", "-b", "readme-truth")
    commit(repo, "fork.md", "only on the fork")
    git(repo, "push", "-q", "-u", "fork", "readme-truth")
    git(repo, "checkout", "-q", "main")
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert run("restore", "--home", str(fresh), "--file", str(out)).returncode == 0
    assert git(fresh / "Data", "remote", "get-url", "fork") == f"file://{fork}"
    assert git(fresh / "Data", "rev-parse", "refs/heads/readme-truth") == git(repo, "rev-parse", "readme-truth")


def test_a_restore_beside_the_original_shares_git_objects(home, tmp_path):
    # Winston's restore on his own box: the volume is on the same filesystem
    # as his home, and 18 GB of history would not fit twice. Clones made from
    # the original share its objects (hardlinks) and still point at origin.
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    r = run("restore", "--home", str(fresh), "--file", str(out), "--reference-root", str(home))
    assert r.returncode == 0, r.stderr
    assert git(fresh / "Data", "rev-parse", "HEAD") == git(home / "Data", "rev-parse", "HEAD")
    assert git(fresh / "Data", "remote", "get-url", "origin") == str(tmp_path / "remotes/data.git")
    objs = lambda root: {p.name: p.stat().st_ino for p in (root / ".git/objects/pack").glob("*.pack")} or \
        {str(p.relative_to(root)): p.stat().st_ino for p in (root / ".git/objects").rglob("*") if p.is_file()}
    shared = set(objs(home / "Data").values()) & set(objs(fresh / "Data").values())
    assert shared, "objects are hardlinked from the original, not copied"


def test_a_detached_head_keeps_every_branch_where_it_was(home, tmp_path):
    # Winston's dips checkout: HEAD detached, local main behind origin/main.
    repo = home / ".datacore/v2-runner"
    first = git(repo, "rev-parse", "HEAD")
    commit(repo, "more.md", "pushed later")
    git(repo, "push", "-q")
    git(repo, "reset", "-q", "--hard", first)    # local main stays behind
    git(repo, "checkout", "-q", "--detach", first)
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert run("restore", "--home", str(fresh), "--file", str(out)).returncode == 0
    assert git(fresh / ".datacore/v2-runner", "rev-parse", "refs/heads/main") == first
    assert git(fresh / ".datacore/v2-runner", "rev-parse", "HEAD") == first


def test_installed_packages_outside_checkouts_are_kept(home, tmp_path):
    # OpenClaw's installed plugins (the Codex harness) live in
    # ~/.openclaw/npm/.../node_modules; skipping every node_modules dropped them
    # from Data's backups, and his restored copy had no Codex.
    plugin = home / ".openclaw/npm/projects/codex/node_modules/@openclaw/codex/dist"
    plugin.mkdir(parents=True)
    (plugin / "index.js").write_text("export {}\n")
    (home / "Data/node_modules/x").mkdir(parents=True, exist_ok=True)
    (home / "Data/node_modules/x/i.js").write_text("cache")
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    names = tarfile.open(out).getnames()
    assert "state/.openclaw/npm/projects/codex/node_modules/@openclaw/codex/dist/index.js" in names
    assert not any(n.startswith("state/Data/node_modules") for n in names), "inside a checkout it is a cache"


def test_a_pods_empty_skeleton_counts_as_an_empty_home(home, tmp_path):
    # A pod that started on the volume (key directory, runtime links, the ssh
    # mount point) leaves directories and symlinks but no files; the proof's
    # restore of Tris refused such a volume as "not empty".
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--exclude", ".hermes/hermes-agent", "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    (fresh / ".datacore/keys").mkdir(parents=True)
    (fresh / ".ssh").mkdir()
    (fresh / ".hermes").mkdir()
    (fresh / ".hermes/hermes-agent").symlink_to("/opt/hermes-agent")
    r = run("restore", "--home", str(fresh), "--file", str(out))
    assert r.returncode == 0, r.stderr
    assert (fresh / ".hermes/state.db").exists()


def test_a_home_with_real_files_is_still_refused(home, tmp_path):
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    (fresh / ".hermes").mkdir(parents=True)
    (fresh / ".hermes/state.db").write_text("someone's state")
    r = run("restore", "--home", str(fresh), "--file", str(out))
    assert r.returncode != 0 and "not empty" in r.stderr


def test_hardlinked_files_restore(home, tmp_path):
    # Tris's browser screenshots are hardlinks of each other; the archive
    # names a hardlink's partner with the state/ prefix the restore strips.
    a = home / ".hermes/cache/shot-a.png"
    a.parent.mkdir(parents=True)
    a.write_bytes(b"png")
    os.link(a, home / ".hermes/cache/shot-b.png")
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    r = run("restore", "--home", str(fresh), "--file", str(out))
    assert r.returncode == 0, r.stderr
    assert (fresh / ".hermes/cache/shot-b.png").read_bytes() == b"png"


def test_head_on_a_branch_deleted_upstream_is_carried(home, tmp_path):
    # Tris's ventures-tmp: HEAD on a branch origin deleted; the local
    # origin/<branch> still had it, so nothing looked unpushed and no bundle
    # was cut -- the restore could not check the commit out.
    repo = home / "Data"
    bare = tmp_path / "remotes/data.git"
    git(repo, "remote", "set-url", "origin", f"file://{bare}")
    git(repo, "checkout", "-q", "-b", "feat/x")
    commit(repo, "x.md", "feature")
    git(repo, "push", "-q", "-u", "origin", "feat/x")
    git(bare, "branch", "-D", "feat/x")
    git(bare, "reflog", "expire", "--expire=now", "--all")
    git(bare, "gc", "-q", "--prune=now")
    out = tmp_path / "b.tar.gz"
    assert run("backup", "--home", str(home), "--out", str(out)).returncode == 0
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    r = run("restore", "--home", str(fresh), "--file", str(out))
    assert r.returncode == 0, r.stderr
    assert git(fresh / "Data", "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")
