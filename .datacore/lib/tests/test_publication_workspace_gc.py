"""A retired publication worktree is reclaimed only when its content is published."""
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import publication_workspace_gc as gc  # noqa: E402
from worktree_lifecycle import allocate_publication_workspace, retire_worktree  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True)
    work = tmp_path / "1-fixture"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    for key, value in (("user.email", "t@t"), ("user.name", "t"),
                       ("core.hooksPath", str(work / ".git" / "hooks"))):
        git(work, "config", key, value)
    (work / "seed.md").write_text("seed\n")
    git(work, "add", "-A"); git(work, "commit", "-qm", "seed")
    git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(work, "branch", "-M", "main")
    git(work, "remote", "set-head", "origin", "main")
    return work


def _retired_worktree(repo: Path, *, publish: bool) -> Path:
    """A worktree retired exactly as run publication retires one."""
    workspace = allocate_publication_workspace(repo)
    checkout = workspace / "worktree"
    git(repo, "worktree", "add", "-q", "--detach", str(checkout), "HEAD")
    (checkout / "report.md").write_text("a deliverable\n")
    git(checkout, "add", "-A"); git(checkout, "commit", "-qm", "published work")
    if publish:
        git(checkout, "push", "-q", "origin", "HEAD:refs/heads/main")
    return retire_worktree(repo, checkout.resolve()).path


def test_a_published_worktree_is_reclaimed_and_its_commit_survives(repo, capsys):
    """5-plur held 19 workspaces of ~737MB inside .git on a host at 99% full:
    retention with no reclamation route is a slow outage."""
    retired = _retired_worktree(repo, publish=True)
    head = git(retired, "rev-parse", "HEAD")
    assert retired.exists()

    assert gc.main(["--repo", str(repo), "--apply"]) == 0

    assert not retired.exists(), "the checkout is a copy, and the copy is what is reclaimed"
    assert git(repo, "cat-file", "-t", head) == "commit", "the commit itself is untouched"
    assert "report.md" in git(repo, "show", "--name-only", "--format=", head)


def test_an_unpublished_worktree_is_left_alone_and_named(repo, capsys):
    retired = _retired_worktree(repo, publish=False)

    assert gc.main(["--repo", str(repo), "--apply"]) == 0

    assert retired.exists(), "this is precisely the copy retention exists for"
    out = capsys.readouterr().out
    assert "RETAINED" in out and "retention exists for" in out


def test_a_dry_run_removes_nothing(repo, capsys):
    retired = _retired_worktree(repo, publish=True)
    assert gc.main(["--repo", str(repo)]) == 0
    assert retired.exists()
    assert "reclaimable" in capsys.readouterr().out


def test_intent_files_and_workspace_directories_are_never_touched(repo):
    retired = _retired_worktree(repo, publish=True)
    workspace = retired.parent.parent
    intent = workspace / "publication-intent.json"
    intent.write_text('{"paths": ["report.md"]}')

    gc.main(["--repo", str(repo), "--apply"])

    assert intent.read_text() == '{"paths": ["report.md"]}'
    assert workspace.is_dir()
