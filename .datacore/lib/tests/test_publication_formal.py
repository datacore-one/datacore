"""Defects the Lean model (specs/datacore-lean/DatacoreSpec/Publication.lean) found.

Each test replays a counterexample from that model against the real code, in
throwaway repositories under tmp_path (the "origin" is a local bare repository).
The model proves the fixed behaviour; these pin the Python to it.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]

import publication_workspace_gc as gc
import worktree_lifecycle
from worktree_lifecycle import allocate_publication_workspace, retire_worktree


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True)
    work = tmp_path / "1-fixture"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    for key, value in (("user.email", "t@t"), ("user.name", "t"),
                       ("core.hooksPath", str(work / ".git" / "hooks"))):
        git(work, "config", key, value)
    (work / "seed.md").write_text("seed\n")
    (work / ".gitignore").write_text("*.log\n")
    git(work, "add", "-A"); git(work, "commit", "-qm", "seed")
    git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(work, "branch", "-M", "main")
    git(work, "remote", "set-head", "origin", "main")
    return work


def _workspace_at_published_base(repo):
    checkout = allocate_publication_workspace(repo) / "worktree"
    git(repo, "worktree", "add", "-q", "--detach", str(checkout), "origin/main")
    return checkout.resolve()


# --- Finding 1: reclaim deleted work that existed only in the checkout --------


def test_failed_publication_commit_leaves_staged_and_untracked_work_that_gc_must_keep(repo):
    """A candidate whose commit hook refused: HEAD is still the published base,
    the captured content is staged, and a late writer added an untracked file.
    HEAD-is-published held, and `git worktree remove --force` deleted both."""
    checkout = _workspace_at_published_base(repo)
    (checkout / "report.md").write_text("captured deliverable\n")
    git(checkout, "add", "report.md")
    (checkout / "late-note.md").write_text("written after the status snapshot\n")
    retired = retire_worktree(repo, checkout).path

    assert gc.main(["--repo", str(repo), "--apply"]) == 0

    assert (retired / "report.md").read_text() == "captured deliverable\n"
    assert (retired / "late-note.md").exists()


def test_ignored_files_are_work_too(repo):
    checkout = _workspace_at_published_base(repo)
    (checkout / "run.log").write_text("the only record of what the run did\n")
    retired = retire_worktree(repo, checkout).path
    gc.main(["--repo", str(repo), "--apply"])
    assert (retired / "run.log").exists()


def test_a_live_unretired_workspace_is_not_reclaimed_mid_publication(repo):
    checkout = _workspace_at_published_base(repo)
    gc.main(["--repo", str(repo), "--apply"])
    assert checkout.exists(), "only a retired checkout has a finished writer"


def test_a_reservation_checkout_whose_branch_advanced_is_still_reclaimed(repo):
    """knowledge_commit's `target` worktree: the branch is advanced by update-ref
    under it, so HEAD moves while index and files stay at the base. That reads
    as dirty in reverse, but every byte is the published base tree."""
    git(repo, "checkout", "-q", "-b", "elsewhere")
    target = allocate_publication_workspace(repo) / "target"
    git(repo, "worktree", "add", "-q", str(target), "main")
    base = git(repo, "rev-parse", "main")
    (repo / "new.md").write_text("published\n")
    git(repo, "add", "new.md")
    tree = git(repo, "write-tree")
    sha = git(repo, "commit-tree", tree, "-p", base, "-m", "publication")
    git(repo, "update-ref", "refs/heads/main", sha, base)
    git(repo, "push", "-q", "origin", f"{sha}:refs/heads/main")
    retired = retire_worktree(repo, target.resolve()).path
    assert git(retired, "status", "--porcelain"), "precondition: reads as dirty in reverse"

    gc.main(["--repo", str(repo), "--apply"])

    assert not retired.exists()


def test_a_clean_published_retired_worktree_is_still_reclaimed(repo):
    checkout = _workspace_at_published_base(repo)
    retired = retire_worktree(repo, checkout).path
    gc.main(["--repo", str(repo), "--apply"])
    assert not retired.exists()


# --- Finding 2: the repo lock was keyed by the path's basename ----------------


def _try_transport_lock(repo, timeout):
    probe = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {str(LIB)!r})
        from pathlib import Path
        import ledger_transport
        ledger_transport._REPO_LOCK_TIMEOUT = {timeout}
        try:
            with ledger_transport._repo_lock(Path({str(repo)!r})):
                pass
            print('ACQUIRED')
        except Exception as exc:
            print('BLOCKED', type(exc).__name__)
    """)
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         timeout=60, env=os.environ.copy())
    return out.stdout.strip() or out.stderr.strip()


def test_a_publication_from_a_linked_worktree_excludes_the_main_checkouts_autosave(repo, tmp_path):
    """The record lives in the shared git-common-dir, so every checkout of the
    repository must take the same lock. `worktree-7` and `1-fixture` did not."""
    linked = tmp_path / "worktree-7"
    git(repo, "worktree", "add", "-q", "--detach", str(linked), "main")
    from ledger_transport import _repo_lock
    with _repo_lock(linked):
        assert _try_transport_lock(repo, 0.5).startswith("BLOCKED")
    assert _try_transport_lock(repo, 5) == "ACQUIRED"


def test_main_checkout_keeps_its_existing_lock_name(repo):
    import ledger_transport
    assert ledger_transport._lock_name(repo) == "1-fixture"
    linked = repo.parent / "elsewhere"
    git(repo, "worktree", "add", "-q", "--detach", str(linked), "main")
    assert ledger_transport._lock_name(linked) == "1-fixture"
    assert ledger_transport._lock_name(repo.parent / "not-a-repo") == "not-a-repo"


# --- Finding 3: a second write queued before the first was processed ----------


@pytest.fixture
def space(tmp_path, monkeypatch):
    import zettel_db
    monkeypatch.setattr(zettel_db, "SPACES", {"test": {"path": tmp_path}})
    with zettel_db.get_connection("test") as conn:
        conn.executescript('''
        CREATE TABLE pending_writes (id INTEGER PRIMARY KEY, table_name TEXT, record_id INTEGER,
          operation TEXT, changes TEXT, target_file TEXT, status TEXT, error_message TEXT, applied_at TEXT);
        CREATE TABLE file_checksums (path TEXT PRIMARY KEY, checksum TEXT, indexed_at TEXT, modified_at TEXT);
        ''')
    target = tmp_path / "next_actions.org"
    target.write_text("* TODO first\n* TODO second\n")
    return target


def _done(target, heading):
    import writeback_store as store
    return store.queue("test", "tasks", 1, str(target), "update_state",
                       {"heading": heading, "old_state": "TODO", "new_state": "DONE"})


def _status(identity):
    import zettel_db
    with zettel_db.get_connection("test") as conn:
        return conn.execute("SELECT status FROM pending_writes WHERE id=?", (identity,)).fetchone()["status"]


@pytest.mark.parametrize("order", ["queued", "reversed"])
def test_two_completions_queued_against_one_file_both_apply(space, order):
    """`writeback_engine --queue` twice, then `--process`: the second shared the
    first's `before`, always conflicted, and `--clear-failed` then deleted it."""
    import writeback_store as store
    first, second = _done(space, "first"), _done(space, "second")
    for identity in ([first, second] if order == "queued" else [second, first]):
        assert store.process(identity, "test")[0]
    assert space.read_text() == "* DONE first\n* DONE second\n"
    assert _status(first) == _status(second) == "completed"


def test_a_third_version_still_conflicts_every_chained_write(space):
    import writeback_store as store
    first, second = _done(space, "first"), _done(space, "second")
    space.write_text("* TODO first\n* WAITING second\nhuman edit\n")
    assert not store.process(first, "test")[0]
    assert not store.process(second, "test")[0]
    assert space.read_text() == "* TODO first\n* WAITING second\nhuman edit\n"
    assert _status(first) == _status(second) == "conflict"


def test_a_write_queued_after_an_external_edit_is_based_on_that_edit(space):
    import writeback_store as store
    first = _done(space, "first")
    space.write_text("* TODO first\n* TODO second\nhuman edit\n")
    second = _done(space, "second")
    assert store.process(second, "test")[0]
    assert _status(first) == "conflict"
    assert space.read_text() == "* TODO first\n* DONE second\nhuman edit\n"


def test_writes_queued_after_a_stale_one_chain_past_it(space):
    import writeback_store as store
    stale = _done(space, "first")
    space.write_text("* TODO first\n* TODO second\n* TODO third\n")
    second, third = _done(space, "second"), _done(space, "third")
    assert store.process(third, "test")[0]
    assert (_status(stale), _status(second), _status(third)) == ("conflict", "completed", "completed")
    assert space.read_text() == "* TODO first\n* DONE second\n* DONE third\n"


# --- Finding 4: retirement's HEAD compare-and-swap ----------------------------


def test_retirement_refuses_when_a_late_commit_moved_head(repo, monkeypatch):
    checkout = _workspace_at_published_base(repo)
    real = worktree_lifecycle._checked
    late = {}

    def interleave(directory, *args):
        if args[:1] == ("update-ref",):
            (Path(directory) / "late.md").write_text("late\n")
            git(directory, "add", "late.md"); git(directory, "commit", "-qm", "late writer")
            late["sha"] = git(directory, "rev-parse", "HEAD")
        return real(directory, *args)

    monkeypatch.setattr(worktree_lifecycle, "_checked", interleave)
    with pytest.raises(RuntimeError, match="inspect"):
        retire_worktree(repo, checkout)
    moved = [p for p in checkout.parent.glob("retired-worktree-*/worktree")]
    assert len(moved) == 1 and git(moved[0], "rev-parse", "HEAD") == late["sha"]
