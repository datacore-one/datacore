"""AGT-14: What I ask Winston to get done gets finished, or I hear why without asking.

Owner, 2026-11-05 (hand-off "I tell Winston and it happens"). The bar for one
request, which this eval holds the components to:
  1. within 15 minutes Winston says who has it;
  2. within 24 hours something other than the doer has checked the result;
  3. the owner has been told the result, or why it is not done, without asking.

A request is a ledger item Winston filed with `asked_by` (the person who asked).
The first real test is three such requests, one each for Miles, Tris and Data,
typed by the owner: this eval proves the parts, never the flow. Components
verified is not the full flow tested.

Kind: deterministic (the watch's timers on fixed clocks; the real dispatcher in
a scratch fleet for the independent check).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import delegation_watch as dw  # noqa: E402

MIN = 60 * 1000
H = 60 * MIN
T0 = 1_800_000_000_000


def request(iid, status, *, created=T0, claimed=None, completed=None, assignee="data", reason=None):
    hist = [f"{created}.0000.winston winston item.create: applied"]
    if claimed:
        hist.append(f"{claimed}.0000.{assignee} {assignee} item.claim: applied")
    if completed:
        hist.append(f"{completed}.0000.{assignee} {assignee} item.complete: applied")
    return NS(id=iid, title=f"request {iid}", status=status, history=hist, closed_reason=reason,
              closed_at=None, payload={"id": iid, "assignee": assignee, "asked_by": "gregor"})


def tell(folded, now_ms, state):
    sent = []
    dw.report_requests(folded, now_ms=now_ms, state_path=state, send=lambda t: sent.append(t) or True)
    return "\n".join(sent)


def test_within_15_minutes_the_owner_hears_who_has_it(tmp_path):
    st = tmp_path / "s.json"
    taken = {"s": NS(items={"a": request("a", "claimed", claimed=T0 + 5 * MIN)})}
    assert "data has it" in tell(taken, T0 + 10 * MIN, st).lower()
    waiting = {"s": NS(items={"b": request("b", "created")})}
    assert tell(waiting, T0 + 10 * MIN, st) == ""                       # not late yet
    late = tell(waiting, T0 + 16 * MIN, st)
    assert "request b" in late and "not picked up" in late.lower()      # 15 min passed: said, unasked


def test_after_24_hours_an_unchecked_result_reaches_the_owner(tmp_path):
    st = tmp_path / "s.json"
    done = {"s": NS(items={"c": request("c", "completed", claimed=T0 + MIN, completed=T0 + 2 * H)})}
    tell(done, T0 + 3 * H, st)                                          # "done, not yet checked"
    late = tell(done, T0 + 25 * H, st)
    assert "request c" in late and "not checked" in late.lower()


def test_the_result_or_the_reason_reaches_the_owner_once(tmp_path):
    st = tmp_path / "s.json"
    folded = {"s": NS(items={
        "d": request("d", "verified", claimed=T0 + MIN, completed=T0 + H),
        "e": request("e", "dismissed", reason="gave up after 3 failed attempts: HTTP 402 from openrouter.ai")}),
    }
    first = tell(folded, T0 + 2 * H, st)
    assert "request d" in first and "checked" in first
    assert "request e" in first and "402" in first
    assert tell(folded, T0 + 3 * H, st) == ""


@pytest.fixture
def fleet(monkeypatch):
    import actor_identity
    import ledger.policy
    from delegation_drill import DelegationDrill, _install_local_executor
    from ledger_chaos_drill import scratch_fleet
    monkeypatch.setattr(ledger.policy, "DEFAULT_POLICY_PATH", ledger.policy.DEFAULT_POLICY_PATH)
    monkeypatch.setattr(actor_identity, "PRINCIPALS", actor_identity.PRINCIPALS)
    saved = dict(os.environ)
    try:
        with scratch_fleet(prefix="agt14-") as root:
            drill = DelegationDrill(root)
            _install_local_executor()
            yield drill.delegation_space("2-datacore"), drill
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_something_other_than_the_doer_checks_the_result(fleet):
    import ledger_claim
    space, drill = fleet
    iid = drill.delegate(space, by="winston", to="miles", title="write K into k.txt",
                         check="test -f k.txt", asked_by="gregor")
    drill.dispatch(space, "miles")
    assert drill.item(space, iid).status == "completed"
    ledger_claim.main(["--space", str(space), "--actor", "winston", "--verify-others"])
    assert drill.item(space, iid).status == "verified"
