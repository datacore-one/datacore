"""The canary reports the only thing a user cares about: did real work finish.

Every defect that mattered on 2026-09-18 was found by running the delegation
loop, not by the 3363 unit tests that passed all day beside them. These tests
cover the canary's own verdicts, because a canary that cries wolf -- or that
stays quiet when the loop is dead -- is worse than none.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import delegation_canary as canary  # noqa: E402
from ledger.log import EventLog  # noqa: E402
from ledger.policy import guarded_append  # noqa: E402


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setattr(canary, "RESULT", tmp_path / "canary.json")
    import actor_identity
    monkeypatch.setattr(actor_identity, "PRINCIPALS", tmp_path / "absent.yaml")
    actor_identity._PRINCIPALS_CACHE.clear()


def _space(tmp_path):
    space = tmp_path / "1-space"
    space.mkdir(parents=True, exist_ok=True)
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "c@example.invalid"],
                 ["config", "user.name", "c"], ["config", "core.hooksPath", "/dev/null"]):
        subprocess.run(["git", "-C", str(space), *args], check=True, capture_output=True)
    (space / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "-C", str(space), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(space), "commit", "-qm", "seed"], check=True, capture_output=True)
    return space


def _args(space, **kw):
    ns = type("A", (), {})()
    ns.space, ns.assignee, ns.max_age_hours = space, kw.get("assignee", "miles"), kw.get("age", 26.0)
    return ns


def test_a_run_commits_its_input_so_the_check_can_read_it(tmp_path):
    """The check runs against a worktree of the agent's commit: an input that
    is not in git is simply not there. That cost a live round to learn."""
    space = _space(tmp_path)
    assert canary.cmd_run(_args(space)) == 0

    tracked = subprocess.run(["git", "-C", str(space), "ls-files"],
                             capture_output=True, text=True).stdout
    assert f"4-outbox/{canary.MARK}/input-" in tracked, tracked


def test_the_artifact_lives_where_the_structure_hook_allows(tmp_path):
    title, check, src = canary._task("miles", "2026-09-19T0000")
    assert src.startswith("4-outbox/"), src
    assert "4-outbox/" in check


def test_an_unfinished_canary_inside_its_budget_does_not_page(tmp_path):
    space = _space(tmp_path)
    canary.cmd_run(_args(space))
    assert canary.cmd_check(_args(space, age=26)) == 0, "a busy fleet must not page anyone"


def test_an_unfinished_canary_past_its_budget_fails(tmp_path):
    space = _space(tmp_path)
    canary.cmd_run(_args(space))
    stale = json.loads(canary.RESULT.read_text())
    stale["at"] = time.time() - 40 * 3600
    canary.RESULT.write_text(json.dumps(stale))

    assert canary.cmd_check(_args(space, age=26)) == 1
    assert json.loads(canary.RESULT.read_text())["verdict"] == "failed"


def test_a_completed_canary_passes(tmp_path):
    space = _space(tmp_path)
    canary.cmd_run(_args(space))
    iid = json.loads(canary.RESULT.read_text())["item"]
    log = EventLog(space, "miles", sign=False)
    log.append("item.claim", {"id": iid, "owner": "miles"})
    log.append("item.complete", {"id": iid, "owner": "miles", "detail": "done"})

    assert canary.cmd_check(_args(space)) == 0
    assert json.loads(canary.RESULT.read_text())["verdict"] == "completed"


def test_a_host_that_has_never_run_one_is_not_a_failure(tmp_path):
    space = _space(tmp_path)
    assert canary.cmd_check(_args(space)) == 0
    assert json.loads(canary.RESULT.read_text())["verdict"] == "unknown"


def test_a_canary_that_could_not_publish_is_blocked_not_failed(tmp_path):
    """An unreachable remote is a condition. Paging for a closed laptop is how
    an alert stops being read."""
    space = _space(tmp_path)
    canary.RESULT.parent.mkdir(parents=True, exist_ok=True)
    canary.RESULT.write_text(json.dumps({"verdict": "blocked", "at": time.time() - 99 * 3600,
                                         "detail": "offline"}))
    assert canary.cmd_check(_args(space)) == 0


def test_a_second_run_does_not_bury_an_unjudged_canary(tmp_path):
    """The defect: --run overwrote RESULT daily, so --check only ever saw a
    canary a couple of hours old and said "still in flight" forever.

    It ran that way from 2026-09-19, aimed at a space no dispatcher for miles
    sweeps, reporting a healthy delegation loop that had never once closed. A
    canary nobody completes has to reach a failed verdict no matter which of the
    two jobs happens to fire first.
    """
    space = _space(tmp_path)
    assert canary.cmd_run(_args(space)) == 0
    first = json.loads(canary.RESULT.read_text())["item"]

    # A second run while the first is still open must not replace it.
    assert canary.cmd_run(_args(space)) == 0
    assert json.loads(canary.RESULT.read_text())["item"] == first


def test_an_abandoned_canary_fails_at_the_next_run_and_stays_failed(tmp_path):
    space = _space(tmp_path)
    assert canary.cmd_run(_args(space)) == 0
    state = json.loads(canary.RESULT.read_text())
    state["at"] = time.time() - 30 * 3600            # seeded thirty hours ago
    canary.RESULT.write_text(json.dumps(state))

    assert canary.cmd_run(_args(space, age=20.0)) == 1
    after = json.loads(canary.RESULT.read_text())
    assert after["verdict"] == "failed"
    assert after["item"] == state["item"], "the failure must name the canary that failed"


def test_the_run_after_a_failure_starts_fresh(tmp_path):
    """A failure stays on disk for one full cycle of the contract that reads
    it, and then the loop is tried again -- otherwise one bad day is permanent."""
    space = _space(tmp_path)
    canary.RESULT.write_text(json.dumps({"verdict": "failed", "at": time.time() - 90000,
                                         "item": "canary-old"}))
    assert canary.cmd_run(_args(space)) == 0
    assert json.loads(canary.RESULT.read_text())["verdict"] == "dispatched"
