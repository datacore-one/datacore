"""The space hook refuses a commit that adds or changes a generated org/next_actions.org (2026-10-29).

WHY. org/next_actions.org is the ledger's projection, rebuilt on every host. When
a writer commits its copy, every host that pulls takes that copy as its task list
and the hourly import refuses the space over headings the ledger never saw: 5-plur
stopped on 2026-10-29 after a GEO refresh committed one (ec889ce6). 6226c57a
refuses a force-added ignored file, but a space where the projection is still
TRACKED (5-plur at that moment) let any edit through. The projection carries a
"Generated from the ledger" header; a commit carrying that content is refused
whether or not the file was tracked. Untracking it (git rm --cached) still passes,
and a hand-written next_actions.org without the header is left alone.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_githooks_credential_store import _install  # noqa: E402

HOOKS_KEY = "core." + "hooksPath"
PROJECTION = ("#+TITLE: plur Next Actions\n"
              "# Generated from the ledger (Phase 1, DIP-0046). Edits here are ingested hourly; the ledger is the record.\n"
              "* TODO Something\n")


def _git(repo, *args, env):
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    return subprocess.run([*g, *args], cwd=repo, env=env, capture_output=True, text=True)


def _space(tmp_path, tracked_projection):
    root = _install(tmp_path)
    repo = root / "5-plur"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_INDEX_FILE", "NIGHTSHIFT_RUN")}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=env, capture_output=True)
    (repo / "org").mkdir()
    (repo / "README.md").write_text("space\n")
    _git(repo, "add", "README.md", env=env)
    if tracked_projection:   # tracked from before the hook existed
        (repo / "org" / "next_actions.org").write_text(PROJECTION)
        _git(repo, "add", "org/next_actions.org", env=env)
    assert _git(repo, "commit", "-q", "-m", "start", env=env).returncode == 0
    _git(repo, "config", HOOKS_KEY, str(root / ".datacore" / "githooks"), env=env)
    return repo, env


def test_an_edit_to_a_tracked_projection_is_refused(tmp_path):
    repo, env = _space(tmp_path, tracked_projection=True)
    (repo / "org" / "next_actions.org").write_text(PROJECTION + "* TODO [#B] Review GEO draft\n")
    _git(repo, "add", "org/next_actions.org", env=env)
    r = _git(repo, "commit", "-q", "-m", "geo refresh", env=env)
    assert r.returncode != 0, "an edited generated projection was committed"
    assert "org/next_actions.org" in r.stdout + r.stderr


def test_adding_a_projection_is_refused(tmp_path):
    repo, env = _space(tmp_path, tracked_projection=False)
    (repo / "org" / "next_actions.org").write_text(PROJECTION)
    _git(repo, "add", "org/next_actions.org", env=env)
    r = _git(repo, "commit", "-q", "-m", "autosave", env=env)
    assert r.returncode != 0


def test_untracking_the_projection_commits(tmp_path):
    repo, env = _space(tmp_path, tracked_projection=True)
    _git(repo, "rm", "-q", "--cached", "org/next_actions.org", env=env)
    r = _git(repo, "commit", "-q", "-m", "untrack the projection", env=env)
    assert r.returncode == 0, (r.stdout + r.stderr)[-400:]


def test_a_hand_written_next_actions_still_commits(tmp_path):
    repo, env = _space(tmp_path, tracked_projection=False)
    (repo / "org" / "next_actions.org").write_text("* TODO A task I keep by hand\n")
    _git(repo, "add", "org/next_actions.org", env=env)
    r = _git(repo, "commit", "-q", "-m", "tasks", env=env)
    assert r.returncode == 0, (r.stdout + r.stderr)[-400:]
