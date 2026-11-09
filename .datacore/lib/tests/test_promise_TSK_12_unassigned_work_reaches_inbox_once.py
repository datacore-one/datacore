"""TSK-12: work nobody is assigned to comes to me once, as an inbox item.

The owner's words: "Work that nobody is assigned to comes to me once, as an
item in my inbox, instead of being noted in a log every 15 minutes."

Evidence (2026-10-28 fleet log review,
2-datacore/1-tracks/ops/reports/2026-10-28-fleet-logs-bug-classes-report.md):
ledger_claim.py:736-742 prints "N addressed to NOBODY -- not dispatched, give
each an assignee: ..." and returns 0. No inbox item or alert is created, and
the same line is printed on every claim run: 468 times in 5 days on plur-claw,
717 on Winston.

Hermetic: a tmp Data tree (owner inbox at 0-personal/org/inbox.org, a team
space holding the ledger), the real ledger_claim.main() run twice with
--execute, as two 15-minute ticks would. Nothing is dispatched, so no executor
or model is reached.
"""
from __future__ import annotations

import sys

import pytest

import ledger_claim
from ledger.log import EventLog
from ledger.policy import guarded_append

TITLE = "Reconcile the September ledger export"


@pytest.fixture(autouse=True)
def _roster(tmp_path_factory, monkeypatch):
    p = tmp_path_factory.mktemp("reg") / "principals.yaml"
    p.write_text("principals:\n"
                 "  gregor: {kind: human}\n"
                 "  winston: {kind: agent, writes_as: [winston]}\n")
    import actor_identity
    monkeypatch.setattr(actor_identity, "PRINCIPALS", p)


def _tree(tmp_path, monkeypatch):
    data = tmp_path / "Data"
    inbox = data / "0-personal" / "org" / "inbox.org"
    inbox.parent.mkdir(parents=True)
    inbox.write_text("#+TITLE: Inbox\n\n", encoding="utf-8")
    space = data / "2-datacore"
    guarded_append(EventLog(space, "winston", sign=False), "item.create",
                   {"id": "item-nobody", "title": TITLE})
    monkeypatch.setenv("DATACORE_ROOT", str(data))
    return space, inbox


def _tick(space, capsys) -> str:
    argv = sys.argv
    sys.argv = ["ledger_claim.py", "--space", str(space), "--actor", "winston", "--execute"]
    try:
        ledger_claim.main()
    finally:
        sys.argv = argv
    return capsys.readouterr().out


def test_an_unassigned_item_becomes_one_inbox_item_not_a_log_line_each_tick(tmp_path, monkeypatch, capsys):
    space, inbox = _tree(tmp_path, monkeypatch)
    first = _tick(space, capsys)
    second = _tick(space, capsys)
    assert "addressed to NOBODY" in first or TITLE in inbox.read_text(), first  # it was seen

    n = inbox.read_text(encoding="utf-8").count(TITLE)
    assert n == 1, (
        f"after two claim runs the unassigned item {TITLE!r} is in the owner's inbox {n} times "
        f"(want exactly once); ledger_claim only logged: {first.strip()[:160]!r}")
    assert "addressed to NOBODY" not in second, (
        "the second claim run repeated the 'addressed to NOBODY' log line for work already "
        f"handed to the owner: {second.strip()[:160]!r}")
