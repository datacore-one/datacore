"""sprint_files: one discovery path, read from the integration branch, drift named.

Built on real temporary git repos (a bare `origin` plus a clone), because the
defect this guards is about WHICH COPY of a file gets read.

## What would have to break for these to fail

 - discovery reads the working tree again → the feature-branch test sees the
   stale `review` instead of the merged `done`.
 - flat `sprints/<id>.yaml` files drop out of discovery → the layout test
   loses its flat sprint (the reason W27 was skipped on 2026-09-04).
 - linked worktrees are read as extra repos → a sprint appears twice.
 - health() stops checking PRs, or stops naming expired / closed-in-flight
   sprints → the matching health test finds no problem line.
 - a failed PR lookup starts counting as fine → the unverified test fails.
"""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest
import yaml

import sprint_files as sfm


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def sprint(sid: str, status: str = "active", end: str = "2099-01-01", items=None, **extra) -> str:
    doc = {"sprint_id": sid, "status": status,
           "dates": {"start": "2026-01-01", "end": end},
           "backlog": items if items is not None else [{"id": "B1", "state": "ready"}]}
    doc.update(extra)
    return yaml.safe_dump(doc, sort_keys=False)


@pytest.fixture
def space(tmp_path: Path):
    """<root>/5-x/2-projects/repo: a clone whose origin has a `development` branch."""
    root = tmp_path / "Data"
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        git(seed, "config", k, v)
    (seed / "sprints" / "S1").mkdir(parents=True)
    (seed / "sprints" / "S1" / "sprint.yaml").write_text(
        sprint("S1", items=[{"id": "B1", "state": "review", "pr": "acme/app#7"}]))
    (seed / "sprints" / "S0-flat.yaml").write_text(sprint("S0-flat", status="closed"))
    (seed / "sprints" / "sprint.schema.json").write_text("{}")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "seed")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "main")
    git(seed, "checkout", "-qb", "development"); git(seed, "push", "-q", "origin", "development")

    repo = root / "5-x" / "2-projects" / "repo"
    repo.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        git(repo, "config", k, v)
    return {"root": root, "repo": repo, "seed": seed}


def ids(disc) -> list[str]:
    return [s.sprint_id for s in disc.sprints]


def test_reads_both_layouts_and_skips_non_sprint_files(space):
    disc = sfm.discover("5-x", root=space["root"])
    assert ids(disc) == ["S0-flat", "S1"], disc.warnings


def test_reads_the_integration_branch_not_the_checked_out_branch(space):
    """The 2026-09-25 case: the fix is on development, the checkout is elsewhere."""
    seed, repo = space["seed"], space["repo"]
    git(repo, "checkout", "-qb", "feature/unrelated", "origin/main")   # stale copy on disk
    p = seed / "sprints" / "S1" / "sprint.yaml"
    p.write_text(sprint("S1", items=[{"id": "B1", "state": "done", "pr": "acme/app#7"}]))
    git(seed, "commit", "-qam", "close B1"); git(seed, "push", "-q", "origin", "development")

    on_disk = yaml.safe_load((repo / "sprints/S1/sprint.yaml").read_text())
    assert on_disk["backlog"][0]["state"] == "review"          # the trap is armed
    disc = sfm.discover("5-x", root=space["root"])             # fetches
    s1 = next(s for s in disc.sprints if s.sprint_id == "S1")
    assert s1.data["backlog"][0]["state"] == "done"
    assert "origin/development" in s1.where


def test_falls_back_to_default_branch_without_development(space):
    git(space["repo"], "branch", "-rd", "origin/development")
    disc = sfm.discover("5-x", root=space["root"], fetch=False)
    assert ids(disc) == ["S0-flat", "S1"]
    assert all("origin/main" in s.where for s in disc.sprints)


def test_a_linked_worktree_is_not_a_second_repo(space):
    repo = space["repo"]
    git(repo, "worktree", "add", "-q", str(repo.parent / "repo-wt"), "origin/main")
    disc = sfm.discover("5-x", root=space["root"], fetch=False)
    assert ids(disc) == ["S0-flat", "S1"]


def test_no_remote_reads_working_tree_and_says_so(tmp_path):
    repo = tmp_path / "Data" / "5-x" / "2-projects" / "local"
    (repo / "sprints" / "L1").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "sprints" / "L1" / "sprint.yaml").write_text(sprint("L1"))
    disc = sfm.discover("5-x", root=tmp_path / "Data")
    assert ids(disc) == ["L1"]
    assert any("working tree" in w for w in disc.warnings)


# ── health ──────────────────────────────────────────────────────────────────

def disc_of(*docs: str):
    d = sfm.Discovery()
    for text in docs:
        data = yaml.safe_load(text)
        d.sprints.append(sfm.SprintFile(data["sprint_id"], data, "mem", Path("mem")))
    return d


TODAY = date(2026, 9, 25)


def test_expired_active_sprint_is_named():
    out = sfm.health(disc_of(sprint("S10", end="2026-09-13")), today=TODAY, pr_lookup=None)
    assert any("S10" in p and "12 days ago" in p for p in out), out


def test_in_flight_item_in_a_closed_sprint_is_named_unless_carried():
    items = [{"id": "B1", "state": "review"}, {"id": "B2", "state": "claimed"}]
    out = sfm.health(disc_of(sprint("W23", status="closed", items=items,
                                    carryover=["W23#B2"])), today=TODAY, pr_lookup=None)
    assert any(p.startswith("W23#B1") for p in out), out
    assert not any(p.startswith("W23#B2") for p in out), out


def test_review_item_with_merged_pr_is_named_stale():
    items = [{"id": "B1", "state": "review", "pr": "github:plur-ai/enterprise/pull/253"}]
    seen = []

    def lookup(repo, n):
        seen.append((repo, n))
        return {"state": "MERGED", "mergedAt": "2026-06-09T09:55:36Z"}

    out = sfm.health(disc_of(sprint("W23", items=items)), today=TODAY, pr_lookup=lookup)
    assert seen == [("plur-ai/enterprise", 253)]
    assert any("merged 2026-06-09" in p and "stale" in p for p in out), out


def test_open_pr_is_not_a_problem():
    items = [{"id": "B1", "state": "review", "pr": "plur-ai/enterprise#407"}]
    out = sfm.health(disc_of(sprint("W27", items=items)), today=TODAY,
                     pr_lookup=lambda r, n: {"state": "OPEN"})
    assert out == []


def test_failed_pr_lookup_is_unverified_not_clean():
    items = [{"id": "B1", "state": "review", "pr": "https://github.com/acme/app/pull/9"}]

    def boom(repo, n):
        raise RuntimeError("gh: not logged in")

    out = sfm.health(disc_of(sprint("S", items=items)), today=TODAY, pr_lookup=boom)
    assert any("UNVERIFIED" in p for p in out), out


@pytest.mark.parametrize("value,expected", [
    ("github:plur-ai/enterprise/pull/253", ("plur-ai/enterprise", 253)),
    ("https://github.com/plur-ai/enterprise/pull/91", ("plur-ai/enterprise", 91)),
    ('"plur-ai/enterprise#407"', ("plur-ai/enterprise", 407)),
    ("org:org-20260615-gh-token-reauth", None),
    (None, None),
])
def test_parse_pr_forms_used_in_sprint_files(value, expected):
    assert sfm.parse_pr(value) == expected


# ── standup inputs: a merged PR is not in flight ───────────────────────────

def test_standup_moves_merged_review_items_out_of_in_flight():
    import sprint_standup_inputs as ssi
    data = yaml.safe_load(sprint("W23", items=[
        {"id": "B1", "state": "review", "pr": "github:plur-ai/enterprise/pull/253"},
        {"id": "B2", "state": "review", "pr": "plur-ai/enterprise#999"},
        {"id": "B3", "state": "claimed"},
    ]))
    states = {253: "MERGED", 999: "OPEN"}
    out = ssi.extract(data, TODAY, pr_lookup=lambda r, n: {"state": states[n]})
    assert [e["id"] for e in out["stale"]] == ["B1"]
    assert [e["id"] for e in out["in_flight"]] == ["B2", "B3"]


def test_standup_default_refuses_an_expired_sprint(space, monkeypatch):
    """No running sprint must be said, not substituted with the newest file."""
    import sprint_standup_inputs as ssi
    seed = space["seed"]
    (seed / "sprints" / "S1" / "sprint.yaml").write_text(sprint("S1", end="2026-09-13"))
    git(seed, "commit", "-qam", "expire"); git(seed, "push", "-q", "origin", "development")
    monkeypatch.setattr(sfm, "REPO", space["root"])
    data, why = ssi._running_sprint("5-x", TODAY)
    assert data is None and "S1" in why and "past their end date" in why
