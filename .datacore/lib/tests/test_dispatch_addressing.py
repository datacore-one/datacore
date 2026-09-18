"""An item addressed to an executor's own log is that executor's to take.

`ledger_claim.main()` decides who may be offered an item. It compared the
payload's `assignee` to the actor as a STRING, while `ledger/policy.py` -- the
gate that same claim is written through -- resolved both names to principals.
The two answers diverge exactly where a principal has more than one writer
name, which is the ordinary case in this installation: `miles` writes as
`miles` and, on the overnight executor, as `nightshift`.

So an item addressed to `nightshift` was declined by nightshift's own
dispatcher (running as `miles`), and by every other host as well. A decline
writes no event: the item stays `created` with nothing to alert on, and the
gate that would have allowed the claim is never reached. These tests pin the
dispatcher and the gate to the same answer.
"""
from __future__ import annotations

import pytest

import ledger_claim
from ledger.log import EventLog
from ledger.policy import PolicyError, guarded_append


@pytest.fixture(autouse=True)
def _roster(tmp_path_factory, monkeypatch):
    p = tmp_path_factory.mktemp("reg") / "principals.yaml"
    p.write_text("principals:\n"
                 "  miles: {kind: agent, writes_as: [miles, nightshift]}\n"
                 "  winston: {kind: agent, writes_as: [winston, bridge]}\n")
    import actor_identity
    monkeypatch.setattr(actor_identity, "PRINCIPALS", p)


def _space(tmp_path, assignee, actor="winston"):
    space = tmp_path / "space"
    guarded_append(EventLog(space, actor, sign=False), "item.create",
                   {"id": "item-1", "title": "Reconcile the ledger", "assignee": assignee})
    return space


def _plan(space, actor, capsys, execute=False):
    import sys
    argv = sys.argv
    sys.argv = ["ledger_claim.py", "--space", str(space), "--actor", actor]
    if execute:
        sys.argv.append("--execute")
    try:
        assert ledger_claim.main() == 0
    finally:
        sys.argv = argv
    return capsys.readouterr().out


def test_an_executors_own_log_name_reaches_its_own_dispatcher(tmp_path, capsys):
    space = _space(tmp_path, assignee="nightshift")
    out = _plan(space, "miles", capsys)
    assert "would claim" in out, out
    assert "addressed to another agent" not in out


def test_another_principals_item_is_still_declined(tmp_path, capsys):
    space = _space(tmp_path, assignee="nightshift")
    out = _plan(space, "winston", capsys)
    assert "nothing to dispatch" in out
    assert "1 addressed to another agent" in out


def test_the_gate_agrees_with_the_dispatcher_that_offered_it(tmp_path):
    # The dispatcher offering an item the gate then refuses is the failure this
    # pair exists to prevent: work is selected, claimed, then rejected at write.
    # Each half is asked about its own fresh item, because a claim that lands
    # changes the item's status and the next refusal would name that instead.
    accepted = _space(tmp_path / "a", assignee="nightshift")
    guarded_append(EventLog(accepted, "miles", sign=False), "item.claim",
                   {"id": "item-1", "owner": "miles"})

    refused = _space(tmp_path / "b", assignee="nightshift")
    with pytest.raises(PolicyError, match="assigned to another principal"):
        guarded_append(EventLog(refused, "winston", sign=False), "item.claim",
                       {"id": "item-1", "owner": "winston"})


def test_winston_may_address_work_to_the_executor_that_runs_it(tmp_path):
    # `may_delegate_to` lists principals; `nightshift` is a writer name miles
    # owns. Matched as strings, the creation itself was refused -- so the
    # dispatcher never got the chance to decline it, and the delegation path
    # this whole pair guards could not be used at all.
    from claim_gate import check_create

    class _P:
        principals = {"winston": {"may_delegate_to": ["miles", "tris", "data"]}}

    ok, why = check_create("winston", {"title": "t", "assignee": "nightshift"}, policy=_P())
    assert ok, why
    ok, why = check_create("winston", {"title": "t", "assignee": "bridge"}, policy=_P())
    assert ok, why      # winston's own second log is not a delegation at all
    ok, why = check_create("winston", {"title": "t", "assignee": "nightshfit"}, policy=_P())
    assert not ok and "may not delegate" in why      # a typo is still a stranger


# --- Addressed to nobody --------------------------------------------------

def test_an_item_addressed_to_nobody_is_not_dispatched(tmp_path, capsys):
    # First-come is the race, not a mitigation of it: both hosts claim, both
    # run the model, and the loser's completion folds to a no-op.
    space = _space(tmp_path, assignee=None)

    out = _plan(space, "winston", capsys)

    assert "would claim" not in out
    assert "addressed to NOBODY" in out
    assert "Reconcile the ledger" in out, "the operator must be told which item"


def test_it_is_refused_on_every_host_not_raced_for(tmp_path, capsys):
    space = _space(tmp_path, assignee=None)
    for who in ("winston", "miles"):
        assert "would claim" not in _plan(space, who, capsys)


def test_addressing_it_makes_it_dispatchable_again(tmp_path, capsys):
    space = _space(tmp_path, assignee="miles")
    assert "would claim" in _plan(space, "miles", capsys)


# --- The dispatcher's own journal ------------------------------------------

def test_the_run_journal_does_not_dirty_the_tree_it_verifies(tmp_path):
    """`_journal` writes into the tree `_artifact_tree_clean` insists is clean.

    Left uncommitted, it is a path that is neither a ledger append nor the
    agent's artifact, so the NEXT run in that space failed every check closed
    -- "commit task changes before artifact verification" -- about a file the
    dispatcher itself had put there. Until an hourly converge autosaved it, a
    space could not complete a delegated item twice in a row.
    """
    import subprocess
    import ledger_claim
    space = tmp_path / "space"
    (space / "journal").mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "d@example.invalid"],
                 ["config", "user.name", "d"], ["config", "core.hooksPath", "/dev/null"]):
        subprocess.run(["git", "-C", str(space), *args], check=True, capture_output=True)
    (space / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "-C", str(space), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(space), "commit", "-qm", "seed"], check=True, capture_output=True)
    assert ledger_claim._artifact_tree_clean(space)

    ledger_claim._journal(space, "miles", ["did a thing"])

    assert ledger_claim._artifact_tree_clean(space), \
        subprocess.run(["git", "-C", str(space), "status", "--short"],
                       capture_output=True, text=True).stdout


def test_it_commits_only_the_journal(tmp_path):
    """`git add -A` here would sweep up whatever the agent left and commit it
    as though it had been verified, which is the opposite of the point."""
    import subprocess
    import ledger_claim
    space = tmp_path / "space"
    (space / "journal").mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "d@example.invalid"],
                 ["config", "user.name", "d"], ["config", "core.hooksPath", "/dev/null"]):
        subprocess.run(["git", "-C", str(space), *args], check=True, capture_output=True)
    (space / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "-C", str(space), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(space), "commit", "-qm", "seed"], check=True, capture_output=True)
    (space / "agent-leftover.txt").write_text("not verified\n")

    ledger_claim._journal(space, "miles", ["did a thing"])

    tracked = subprocess.run(["git", "-C", str(space), "ls-files"],
                             capture_output=True, text=True).stdout.split()
    assert "agent-leftover.txt" not in tracked, tracked
    assert any(t.startswith("journal/") for t in tracked), tracked


def test_one_items_leftover_does_not_burn_a_model_call_on_the_next(tmp_path, capsys):
    """`_artifact_tree_clean` looks at the whole space, not at one item's
    artifact. So an item whose agent left work uncommitted fails every item
    dispatched after it, each spending a full model call for a result that
    cannot be verified. Observed on nightshift 2026-09-18: two items, the
    second failed on the first one's leftover, both answers correct."""
    import subprocess
    space = tmp_path / "space"
    for n in (1, 2):
        guarded_append(EventLog(space, "winston", sign=False), "item.create",
                       {"id": f"item-{n}", "title": f"task {n}", "assignee": "miles",
                        "check": "true"})
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "d@example.invalid"],
                 ["config", "user.name", "d"], ["config", "core.hooksPath", "/dev/null"]):
        subprocess.run(["git", "-C", str(space), *args], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(space), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(space), "commit", "-qm", "seed"], check=True, capture_output=True)
    # What an agent that did the work but could not commit leaves behind.
    (space / "left-behind.txt").write_text("uncommitted\n")

    out = _plan(space, "miles", capsys, execute=True)

    assert "STOPPING" in out, out
    assert "left-behind.txt" in out, out
    assert "commit or discard it" in out
