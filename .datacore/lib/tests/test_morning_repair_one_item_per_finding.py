"""delegate() reuses any existing open ledger item for a finding across days.

Acceptance test for audit 2026-10-17, section 3 item 3: a finding that fails
on N consecutive days must accumulate ONE open repair item, not one per day.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
for p in (LIB, LIB / "jobs"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import morning_repair as M


def _make_space(root):
    space = root / "2-datacore"
    (space / ".datacore" / "events").mkdir(parents=True)
    return space


def _seed_item(space, iid):
    from ledger.log import EventLog
    EventLog(space, "miles").append("item.create", {
        "id": iid,
        "title": f"Repair: {iid}",
        "assignee": "miles",
        "route": "dev",
        "body": "morning repair item",
        "requested_by": "miles",
        "morning_repair": True,
    })


def test_delegate_still_creates_when_no_prior_item(tmp_path, monkeypatch):
    """Baseline: when no prior ledger item exists, delegate() creates one as before."""
    import ledger.policy

    _make_space(tmp_path)
    monkeypatch.setattr(M, "ROOT", tmp_path)
    monkeypatch.setattr("actor_identity.this_actor", lambda: "miles")
    monkeypatch.setattr(M, "_repairer", lambda: "miles")

    appended = []
    monkeypatch.setattr(ledger.policy, "guarded_append",
                        lambda log, kind, payload: appended.append(payload))

    finding = {"id": "v2-egress-declared", "kind": "v2", "title": "v2-verify: egress declared"}
    iid = M.delegate(dict(finding), "2026-10-16")

    assert iid == "repair-v2-egress-declared-20261016"
    assert len(appended) == 1 and appended[0]["id"] == iid


def test_delegate_reuses_existing_open_item_on_second_day(tmp_path, monkeypatch):
    """A finding already open in the ledger must not generate a second item.

    Simulates two consecutive sweeps with the same finding:
    - day 1 item is seeded directly into the ledger (as the first sweep would have done)
    - day 2 delegate() call must return the day-1 item id WITHOUT appending item.create
    """
    import ledger.policy

    space = _make_space(tmp_path)
    monkeypatch.setattr(M, "ROOT", tmp_path)
    monkeypatch.setattr("actor_identity.this_actor", lambda: "miles")
    monkeypatch.setattr(M, "_repairer", lambda: "miles")

    day1_iid = "repair-v2-egress-declared-20261016"
    _seed_item(space, day1_iid)

    appended = []
    monkeypatch.setattr(ledger.policy, "guarded_append",
                        lambda log, kind, payload: appended.append(payload))

    finding = {"id": "v2-egress-declared", "kind": "v2", "title": "v2-verify: egress declared"}
    iid2 = M.delegate(dict(finding), "2026-10-17")

    assert iid2 == day1_iid, (
        f"expected day-2 delegate() to reuse the live day-1 item {day1_iid!r}, got {iid2!r}"
    )
    assert appended == [], (
        "delegate() must not append a new item.create when a live item already exists for the finding"
    )
