"""git_compact keeps repositories packed without losing anything (owner, 2026-11-14).

WHY. Git packs a repository (stores versions as differences) only when its object
COUNT passes a threshold, and plumbing commits (hash-object, commit-tree) never
trigger even that. The memory store re-committed a 30 MB file every two hours and
reached 5.3 GB in loose objects; packed it is 135 MB. Compaction must never cost
history: every commit, reflog entry and unreachable object survives it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import git_compact  # noqa: E402

ENV = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_INDEX_FILE")}
ENV.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")


def _g(repo, *args, input=None):
    g = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "gc.auto=0", "-c", "maintenance.auto=false"]
    return subprocess.run([*g, *args], input=input, capture_output=True, text=True, env=ENV,
                          check=True).stdout.strip()


def _repo(tmp_path, name="r", commits=30):
    r = tmp_path / name
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True, env=ENV)
    _g(r, "config", "gc.auto", "0")
    body = "\n".join(f"line {i} " + "x" * 60 for i in range(3000))
    for i in range(commits):
        (r / "big.txt").write_text(f"{body}\nversion {i}\n")
        _g(r, "add", "big.txt")
        _g(r, "commit", "-q", "-m", f"v{i}")
    return r


def _loose(repo):
    out = dict(l.split(": ", 1) for l in _g(repo, "count-objects", "-v").splitlines())
    return int(out["count"]), int(out["size"])


def test_compaction_keeps_every_commit_reflog_entry_and_unreachable_object(tmp_path):
    r = _repo(tmp_path)
    dangling = _g(r, "hash-object", "-w", "--stdin", input="nothing points at this blob\n")
    commits, reflog = _g(r, "rev-list", "--all", "--count"), len(_g(r, "log", "-g", "--format=%h").splitlines())
    before_count, _ = _loose(r)
    assert before_count > 50

    git_compact.compact(r)

    assert _g(r, "rev-list", "--all", "--count") == commits
    assert len(_g(r, "log", "-g", "--format=%h").splitlines()) == reflog
    subprocess.run(["git", "-C", str(r), "cat-file", "-e", dangling], check=True, env=ENV)
    assert _loose(r)[0] == 0


def test_a_light_repository_is_left_alone(tmp_path):
    r = _repo(tmp_path, commits=3)
    before = _loose(r)
    assert git_compact.pack_if_heavy(r, loose_mb=50, max_packs=20) == "skipped"
    assert _loose(r) == before


def test_loose_objects_past_the_limit_are_packed_without_losing_commits(tmp_path):
    r = _repo(tmp_path)
    commits = _g(r, "rev-list", "--all", "--count")
    assert git_compact.pack_if_heavy(r, loose_mb=0, max_packs=20) in ("repacked", "compacted")
    assert _loose(r)[0] == 0
    assert _g(r, "rev-list", "--all", "--count") == commits


def test_find_repos_reaches_nested_repositories_and_skips_dependency_folders(tmp_path):
    a = _repo(tmp_path, "space", commits=1)
    b = _repo(a / "2-projects", "app", commits=1)
    _repo(a / "node_modules", "dep", commits=1)
    found = {Path(p).resolve() for p in git_compact.find_repos([tmp_path])}
    assert found == {a.resolve(), b.resolve()}


def test_compaction_lowers_the_automatic_packing_thresholds(tmp_path):
    """Porcelain commits and fetches then pack the repository long before 6,700 loose objects."""
    r = _repo(tmp_path, commits=2)
    subprocess.run(["git", "-C", str(r), "config", "--unset", "gc.auto"], env=ENV)
    git_compact.compact(r)
    def saved(key):  # read the repository's own config, not the -c overrides _g passes
        return subprocess.run(["git", "-C", str(r), "config", "--local", "--get", key],
                              capture_output=True, text=True, env=ENV).stdout.strip()
    assert saved("gc.auto") == str(git_compact.AUTO_LOOSE)
    assert saved("gc.autoPackLimit") == str(git_compact.AUTO_PACKS)
