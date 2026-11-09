"""SYN-12: Agents' routine bookkeeping never buries real changes: one run makes at
most one record of itself.

Evidence (2026-10-28 fleet-logs report): 2-datacore on 10-21 had 3,352
"ledger: autosave before converge" commits and 2,329 "journal: miles ledger
claim run" commits. `ledger_claim.py:254-272` (`_journal`) appends a journal
entry AND commits it on every run that had an outcome, while the run's own
ledger events (`item.claim`, `item.release`) are left uncommitted for a second
commit -- the dispatcher tick's "ledger: <actor> claim/completion" or the next
converge's autosave (`ledger_transport.py:1096`). So every run leaves two
records of itself in the space's history, and a host retrying a failing item
every 15 minutes buries the real changes under them.

Kind: deterministic, hermetic. The REAL dispatcher (`ledger_claim.main`) runs in
a scratch fleet (a tmp git repo per space; the drill's local executor stands in
for the model), as in test_ledger_claim_do_and_start_time.

What counts as a record of the run: each commit the run adds to the space's
history, plus one more if the run leaves its own bookkeeping (journal, ledger
events) uncommitted -- that is the commit somebody else makes for it next.
The agent's own result commit is not bookkeeping and is not produced here: the
task fails without touching the tree.

Graded:
- a run that dispatches one item which fails makes at most one record;
- a run that finds nothing to do makes no record at all (control).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_promise_NS_9_waiting_on_owner_is_not_a_failure import fleet  # noqa: E402,F401


def _git(space, *args) -> str:
    return subprocess.run(["git", "-C", str(space), *args], capture_output=True,
                          text=True, timeout=60).stdout


def _settle(space) -> None:
    """Commit everything present before the run, so only the run's own writes show."""
    _git(space, "add", "-A")
    _git(space, "commit", "-qm", "fixture: before the run", "--allow-empty")


def _records(space, before: str) -> tuple[int, list[str], list[str]]:
    commits = [l for l in _git(space, "log", "--format=%s", f"{before}..HEAD").splitlines() if l]
    dirty = [l[3:] for l in _git(space, "status", "--porcelain", "--untracked-files=all").splitlines() if l]
    return len(commits) + (1 if dirty else 0), commits, dirty


def test_a_run_with_one_failed_item_makes_at_most_one_record(fleet):  # noqa: F811
    root, space, drill = fleet
    drill.delegate(space, by="winston", to="miles", id="syn12-fail",
                   title="fail", check="test -f never.txt")
    _settle(space)
    before = _git(space, "rev-parse", "HEAD").strip()
    out = drill.dispatch(space, "miles")
    assert "FAILED" in out, f"the item was not dispatched: {out[:400]}"
    n, commits, dirty = _records(space, before)
    assert n <= 1, (
        f"one claim run left {n} records of itself: commits {commits}"
        + (f" and uncommitted bookkeeping {dirty} for another commit" if dirty else ""))


def test_a_run_with_nothing_to_do_makes_no_record(fleet):  # noqa: F811
    root, space, drill = fleet
    _settle(space)
    before = _git(space, "rev-parse", "HEAD").strip()
    out = drill.dispatch(space, "miles")
    assert "nothing to dispatch" in out, out[:400]
    n, commits, dirty = _records(space, before)
    assert n == 0, f"a run with nothing to do left records: commits {commits}, uncommitted {dirty}"
