"""A claim run that could not do its job says so in a form a contract can read.

Audit 2026-11-15, section 4.1 items 4, 5, 6 and 8. The claim job's contract
asked only for "no crash and a recent log", so it was green when nothing was
done. And three things made "nothing was done" the normal case without anyone
being told:

  * a refused item (side effects, code outside the space) stayed at the front
    of the queue and was refused again every run -- two of them filled the
    run's limit and blocked everything behind them;
  * a claim whose run crashed stayed claimed forever;
  * the run ended 0 whatever happened.

Now: refusals do not use up the limit; this writer's own claims older than the
limit are released (not counted as an attempt at the task); and the run prints
one `CLAIM-STATUS` line -- OK, or ATTENTION with the reason -- and exits 3 on
ATTENTION.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import ledger_claim
from ledger.log import EventLog, read_events
from ledger.policy import guarded_append


@pytest.fixture(autouse=True)
def _roster(tmp_path_factory, monkeypatch):
    p = tmp_path_factory.mktemp("reg") / "principals.yaml"
    p.write_text("principals:\n"
                 "  miles: {kind: agent, writes_as: [miles, nightshift]}\n"
                 "  winston: {kind: agent, writes_as: [winston, bridge]}\n")
    import actor_identity
    monkeypatch.setattr(actor_identity, "PRINCIPALS", p)


def _git(space, *args):
    return subprocess.run(["git", "-C", str(space), *args], check=True, capture_output=True, text=True).stdout


def _space(tmp_path, items):
    space = tmp_path / "2-team"
    space.mkdir(exist_ok=True)
    for payload in items:
        log = EventLog(space, "winston", sign=False)
        if payload.get("effects"):
            # Written straight to the log: the creation gate asks for an approval
            # these fixtures do not model, and the claim loop's own refusal of a
            # side-effect item is what is under test.
            log.append("item.create", {"assignee": "miles", **payload})
        else:
            guarded_append(log, "item.create", {"assignee": "miles", **payload})
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "d@example.invalid"],
                 ["config", "user.name", "d"]):
        _git(space, *args)
    (space / ".gitignore").write_text(".datacore/state/*\n")
    _git(space, "add", "-A")
    _git(space, "commit", "-qm", "seed")
    return space


def _dispatch(space, capsys, monkeypatch, *extra):
    calls = []

    def run(title, route, cwd, item_id="", hops=0, actor=""):
        calls.append(item_id)
        (Path(cwd) / f"{item_id}.txt").write_text("done\n")
        return True, "did it", {"executor": "fake", "model": "m", "cost_cents": 0, "duration_s": 0}
    monkeypatch.setattr(ledger_claim, "run_task", run)
    argv = sys.argv
    sys.argv = ["ledger_claim.py", "--space", str(space), "--actor", "miles", "--execute", *extra]
    try:
        rc = ledger_claim.main()
    finally:
        sys.argv = argv
    return rc, capsys.readouterr().out, calls


def _status_line(out):
    lines = [ln for ln in out.splitlines() if ln.startswith("CLAIM-STATUS ")]
    assert len(lines) == 1, out
    return lines[0]


def test_a_clean_run_reports_ok(tmp_path, capsys, monkeypatch):
    space = _space(tmp_path, [{"id": "a-1", "title": "task", "check": "test -f a-1.txt"}])
    rc, out, calls = _dispatch(space, capsys, monkeypatch)
    assert rc == 0 and calls == ["a-1"]
    assert _status_line(out).startswith("CLAIM-STATUS 2-team OK "), out


def test_a_run_with_nothing_to_do_reports_ok(tmp_path, capsys, monkeypatch):
    space = _space(tmp_path, [])
    rc, out, _ = _dispatch(space, capsys, monkeypatch)
    assert rc == 0
    assert _status_line(out).startswith("CLAIM-STATUS 2-team OK "), out


def test_refused_items_do_not_use_up_the_limit_and_the_run_asks_for_attention(tmp_path, capsys, monkeypatch):
    space = _space(tmp_path, [
        {"id": "a-refused-1", "title": "send the email", "effects": ["email.send"]},
        {"id": "a-refused-2", "title": "send another", "effects": ["email.send"]},
        {"id": "b-real", "title": "task", "check": "test -f b-real.txt"},
    ])
    rc, out, calls = _dispatch(space, capsys, monkeypatch, "--limit", "2")
    assert calls == ["b-real"], "two refused items ahead of it must not block real work"
    line = _status_line(out)
    assert line.startswith("CLAIM-STATUS 2-team ATTENTION ") and "refused=2" in line, line
    assert rc == 3


def test_items_waiting_for_the_owner_do_not_use_up_the_limit(tmp_path, capsys, monkeypatch):
    # 2026-11-28, Miles on dayshift: the two oldest items were repairs waiting
    # for the owner's merge. Each was counted as taken before the waiting
    # check skipped it, so with --limit 2 they filled every run and 41 items
    # behind them -- every delegation to Miles since 2026-11-16 -- never ran.
    space = _space(tmp_path, [
        {"id": "a-wait-1", "title": "Repair one", "check": "test -f merged-1"},
        {"id": "a-wait-2", "title": "Repair two", "check": "test -f merged-2"},
        {"id": "b-real", "title": "task", "check": "test -f b-real.txt"},
    ])
    log = EventLog(space, "miles", sign=False)
    for iid in ("a-wait-1", "a-wait-2"):
        log.append("item.claim", {"id": iid, "owner": "miles", "route": "dev", "reason": "test"})
        log.append("item.release", {"id": iid, "kind": "waiting_on_owner",
                                    "reason": "waiting for you: the PR is open"})
    _git(space, "add", "-A")
    _git(space, "commit", "-qm", "waiting")
    real = ledger_claim._isolated_check_explained

    def check(sp, chk, baseline=None):
        if "merged-" in chk:
            return ledger_claim.WAITING_ON_OWNER, None, "the owner has not merged"
        return real(sp, chk, baseline)
    monkeypatch.setattr(ledger_claim, "_isolated_check_explained", check)

    rc, out, calls = _dispatch(space, capsys, monkeypatch, "--limit", "2")

    assert calls == ["b-real"], out
    assert "waiting=2" in _status_line(out), out


def test_an_unreadable_tree_asks_for_attention(tmp_path, capsys, monkeypatch):
    space = tmp_path / "2-team"
    guarded_append(EventLog(space, "winston", sign=False), "item.create",
                   {"id": "a-1", "title": "task", "assignee": "miles", "check": "true"})
    rc, out, calls = _dispatch(space, capsys, monkeypatch)      # not a git repository
    assert calls == []
    line = _status_line(out)
    assert line.startswith("CLAIM-STATUS 2-team ATTENTION ") and "stopped" in line, line
    assert rc == 3


def test_a_failed_check_is_normal_work_not_a_broken_run(tmp_path, capsys, monkeypatch):
    space = _space(tmp_path, [{"id": "a-1", "title": "task", "check": "false"}])
    rc, out, _ = _dispatch(space, capsys, monkeypatch)
    assert _status_line(out).startswith("CLAIM-STATUS 2-team OK "), out
    assert "failed=1" in _status_line(out)


def _claim_at(space, iid, hlc_ms):
    """A claim by miles, stamped in the past (as a run that then died would leave)."""
    log = EventLog(space, "miles", sign=False)
    ev = log.append("item.claim", {"id": iid, "owner": "miles", "route": "do", "reason": "test"})
    return ev


def test_my_own_stale_claim_is_released_and_not_counted_as_an_attempt(tmp_path, capsys, monkeypatch):
    space = _space(tmp_path, [{"id": "a-1", "title": "task", "check": "test -f a-1.txt"}])
    _claim_at(space, "a-1", 0)
    monkeypatch.setattr(ledger_claim, "STALE_CLAIM_S", 0)       # any existing claim is stale
    rc, out, calls = _dispatch(space, capsys, monkeypatch)
    releases = [e for e in read_events(space) if e.type == "item.release"
                and (e.payload or {}).get("kind") == "stale_claim"]
    assert len(releases) == 1, out
    assert "stale_claim" in ledger_claim.NOT_AN_ATTEMPT
    assert "stale_released=1" in _status_line(out)
    assert calls == ["a-1"], "released at the start of the run, then taken again in the same run"


def test_a_fresh_claim_is_left_alone(tmp_path, capsys, monkeypatch):
    space = _space(tmp_path, [{"id": "a-1", "title": "task", "check": "test -f a-1.txt"}])
    _claim_at(space, "a-1", 0)
    rc, out, calls = _dispatch(space, capsys, monkeypatch)       # default limit: hours
    assert calls == [] and "stale_released" not in _status_line(out)
    assert not [e for e in read_events(space) if e.type == "item.release"]


def test_work_left_claimed_for_the_owners_review_is_never_released_as_stale(tmp_path, capsys, monkeypatch):
    space = _space(tmp_path, [{"id": "a-1", "title": "no check: the owner reviews it"}])
    monkeypatch.setattr(ledger_claim, "STALE_CLAIM_S", 0)
    _dispatch(space, capsys, monkeypatch)                        # runs; left claimed, needs review
    rc, out, calls = _dispatch(space, capsys, monkeypatch)
    assert not [e for e in read_events(space) if e.type == "item.release"], out
    assert calls == []


# ---- the second check belongs to the run, not to one host's schedule ----------
#
# 2026-11-16: the versioned run script made the second check, and one of four
# agents ran that script. The other three called the dispatcher directly from
# their own schedules, so three of four never second-checked anyone's work.

def test_every_executing_run_also_makes_the_second_check(tmp_path, capsys, monkeypatch):
    space = _space(tmp_path, [])
    rc, out, _calls = _dispatch(space, capsys, monkeypatch)
    assert rc == 0
    assert "CLAIM-STATUS 2-team OK" in out
    assert "verified 0, recheck failed 0" in out, out


def test_a_second_check_that_cannot_run_is_said_and_does_not_hide_the_claim_result(tmp_path, capsys, monkeypatch):
    space = _space(tmp_path, [])

    def broken(*a, **k):
        raise RuntimeError("no checker here")
    monkeypatch.setattr(ledger_claim, "verify_others", broken)
    rc, out, _calls = _dispatch(space, capsys, monkeypatch)
    assert "CLAIM-STATUS 2-team OK" in out
    assert "second check could not run" in out and "RuntimeError" in out
    assert rc == 3, "a run that could not second-check is not a clean run"


def test_a_plan_only_run_checks_nothing(tmp_path, capsys, monkeypatch):
    space = _space(tmp_path, [])
    monkeypatch.setattr(sys, "argv", ["ledger_claim.py"])
    ledger_claim.main(["--space", str(space), "--actor", "miles"])
    assert "verified" not in capsys.readouterr().out
