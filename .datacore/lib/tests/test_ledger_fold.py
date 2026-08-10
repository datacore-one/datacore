"""Tests for ledger.fold -- deterministic reduction of events into LedgerState.

`fold` is a pure function: no clock reads, no randomness, no I/O. Events
arrive already merged and hlc-sorted (the caller's contract, per
`ledger.log.read_events`) -- fold() must NOT re-sort. "Earliest wins"
tie-breaking for competing claims falls naturally out of processing the
list strictly in the order given.
"""

import copy

from ledger.events import Event
from ledger.fold import LedgerState, ItemState, fold


def _ev(seq, hlc, actor, type_, payload):
    return Event(seq=seq, hlc=hlc, actor=actor, type=type_, payload=payload, prev="", hash="", sig="")


# --- lifecycle ------------------------------------------------------------


def test_lifecycle_create_claim_complete_verify():
    events = [
        _ev(0, "1.0.mac", "mac", "item.create", {"id": "t1", "title": "Ship it"}),
        _ev(1, "2.0.mac", "mac", "item.claim", {"id": "t1"}),
        _ev(2, "3.0.mac", "mac", "item.complete", {"id": "t1"}),
        _ev(3, "4.0.mac", "mac", "item.verify", {"id": "t1"}),
    ]

    state = fold(events)

    item = state.items["t1"]
    assert item.id == "t1"
    assert item.title == "Ship it"
    assert item.owner == "mac"
    assert item.status == "verified"
    assert len(item.history) == 4
    assert all("no-op" not in h for h in item.history)


# --- competing claims -------------------------------------------------------


def test_competing_claims_earliest_hlc_wins():
    events = [
        _ev(0, "1.0.mac", "mac", "item.create", {"id": "t1", "title": "Race"}),
        _ev(1, "2.0.mac", "mac", "item.claim", {"id": "t1"}),
        _ev(2, "3.0.pi", "pi", "item.claim", {"id": "t1"}),
    ]

    state = fold(events)

    item = state.items["t1"]
    assert item.owner == "mac"
    assert item.status == "claimed"
    # the loser's claim must show up in history as a no-op, not silently vanish
    loser_entries = [h for h in item.history if "pi" in h]
    assert len(loser_entries) == 1
    assert "no-op" in loser_entries[0]


# --- dismiss is terminal ----------------------------------------------------


def test_dismiss_terminal_blocks_later_claim_and_complete():
    events = [
        _ev(0, "1.0.mac", "mac", "item.create", {"id": "t1", "title": "Dead"}),
        _ev(1, "2.0.mac", "mac", "item.dismiss", {"id": "t1"}),
        _ev(2, "3.0.pi", "pi", "item.claim", {"id": "t1"}),
        _ev(3, "4.0.pi", "pi", "item.complete", {"id": "t1"}),
    ]

    state = fold(events)

    item = state.items["t1"]
    assert item.status == "dismissed"
    assert item.owner is None
    claim_entry = [h for h in item.history if "item.claim" in h][0]
    complete_entry = [h for h in item.history if "item.complete" in h][0]
    assert "no-op" in claim_entry
    assert "no-op" in complete_entry


def test_dismiss_terminal_blocks_owner_set():
    """Self-review question: does dismiss block the admin owner.set override too? Yes."""
    events = [
        _ev(0, "1.0.mac", "mac", "item.create", {"id": "t1", "title": "Dead"}),
        _ev(1, "2.0.mac", "mac", "item.dismiss", {"id": "t1"}),
        _ev(2, "3.0.admin", "admin", "owner.set", {"id": "t1", "owner": "someone-else"}),
    ]

    state = fold(events)

    item = state.items["t1"]
    assert item.status == "dismissed"
    assert item.owner is None
    owner_set_entry = [h for h in item.history if "owner.set" in h][0]
    assert "no-op" in owner_set_entry


# --- release ----------------------------------------------------------------


def test_release_by_non_owner_is_noop():
    events = [
        _ev(0, "1.0.mac", "mac", "item.create", {"id": "t1", "title": "Mine"}),
        _ev(1, "2.0.mac", "mac", "item.claim", {"id": "t1"}),
        _ev(2, "3.0.pi", "pi", "item.release", {"id": "t1"}),
    ]

    state = fold(events)

    item = state.items["t1"]
    assert item.owner == "mac"
    assert item.status == "claimed"
    release_entry = [h for h in item.history if "item.release" in h][0]
    assert "no-op" in release_entry


def test_release_by_owner_reopens_item():
    events = [
        _ev(0, "1.0.mac", "mac", "item.create", {"id": "t1", "title": "Mine"}),
        _ev(1, "2.0.mac", "mac", "item.claim", {"id": "t1"}),
        _ev(2, "3.0.mac", "mac", "item.release", {"id": "t1"}),
    ]

    state = fold(events)

    item = state.items["t1"]
    assert item.owner is None
    assert item.status == "created"
    release_entry = [h for h in item.history if "item.release" in h][0]
    assert "no-op" not in release_entry


def test_release_from_completed_by_owner_is_noop():
    """release means un-claim, never un-complete -- legal only from
    status=='claimed'. The owner releasing a completed item must NOT
    silently regress it back to 'created'."""
    events = [
        _ev(0, "1.0.mac", "mac", "item.create", {"id": "t1", "title": "Done"}),
        _ev(1, "2.0.mac", "mac", "item.claim", {"id": "t1"}),
        _ev(2, "3.0.mac", "mac", "item.complete", {"id": "t1"}),
        _ev(3, "4.0.mac", "mac", "item.release", {"id": "t1"}),
    ]

    state = fold(events)

    item = state.items["t1"]
    assert item.status == "completed"
    assert item.owner == "mac"
    release_entry = [h for h in item.history if "item.release" in h][0]
    assert "no-op" in release_entry


def test_release_from_verified_by_owner_is_noop():
    events = [
        _ev(0, "1.0.mac", "mac", "item.create", {"id": "t1", "title": "Done"}),
        _ev(1, "2.0.mac", "mac", "item.claim", {"id": "t1"}),
        _ev(2, "3.0.mac", "mac", "item.complete", {"id": "t1"}),
        _ev(3, "4.0.mac", "mac", "item.verify", {"id": "t1"}),
        _ev(4, "5.0.mac", "mac", "item.release", {"id": "t1"}),
    ]

    state = fold(events)

    item = state.items["t1"]
    assert item.status == "verified"
    assert item.owner == "mac"
    release_entry = [h for h in item.history if "item.release" in h][0]
    assert "no-op" in release_entry


# --- owner.set admin override -----------------------------------------------


def test_owner_set_overrides_regardless_of_claim():
    events = [
        _ev(0, "1.0.mac", "mac", "item.create", {"id": "t1", "title": "Assigned"}),
        _ev(1, "2.0.admin", "admin", "owner.set", {"id": "t1", "owner": "pi"}),
    ]

    state = fold(events)

    item = state.items["t1"]
    assert item.owner == "pi"
    # owner.set is an admin op, not a claim -- status is unaffected
    assert item.status == "created"


# --- spend -------------------------------------------------------------------


def test_spend_accumulates_per_actor():
    events = [
        _ev(0, "1.0.mac", "mac", "spend.record", {"cents": 500, "ref": "invoice-1"}),
        _ev(1, "2.0.pi", "pi", "spend.record", {"cents": 250, "ref": "invoice-2"}),
        _ev(2, "3.0.mac", "mac", "spend.record", {"cents": 100, "ref": "invoice-3"}),
    ]

    state = fold(events)

    assert state.spend == {"mac": 600, "pi": 250}


# --- orphans ------------------------------------------------------------------


def test_orphan_events_land_in_orphans_not_items():
    events = [
        _ev(0, "1.0.pi", "pi", "item.claim", {"id": "ghost"}),
        _ev(1, "2.0.mac", "mac", "item.create", {"id": "t1", "title": "Real"}),
    ]

    state = fold(events)

    assert "ghost" not in state.items
    assert "t1" in state.items
    assert len(state.orphans) == 1
    assert "ghost" in state.orphans[0]
    assert "item.claim" in state.orphans[0]
    assert "1.0.pi" in state.orphans[0]


# --- illegal transitions -----------------------------------------------------


def test_complete_before_claim_is_noop():
    events = [
        _ev(0, "1.0.mac", "mac", "item.create", {"id": "t1", "title": "Unclaimed"}),
        _ev(1, "2.0.mac", "mac", "item.complete", {"id": "t1"}),
    ]

    state = fold(events)

    item = state.items["t1"]
    assert item.status == "created"
    complete_entry = [h for h in item.history if "item.complete" in h][0]
    assert "no-op" in complete_entry


def test_verify_before_complete_is_noop():
    events = [
        _ev(0, "1.0.mac", "mac", "item.create", {"id": "t1", "title": "Unclaimed"}),
        _ev(1, "2.0.mac", "mac", "item.claim", {"id": "t1"}),
        _ev(2, "3.0.mac", "mac", "item.verify", {"id": "t1"}),
    ]

    state = fold(events)

    item = state.items["t1"]
    assert item.status == "claimed"
    verify_entry = [h for h in item.history if "item.verify" in h][0]
    assert "no-op" in verify_entry


# --- duplicate create ---------------------------------------------------------


def test_duplicate_create_is_noop():
    events = [
        _ev(0, "1.0.mac", "mac", "item.create", {"id": "t1", "title": "First", "owner": "mac"}),
        _ev(1, "2.0.pi", "pi", "item.create", {"id": "t1", "title": "Second", "owner": "pi"}),
    ]

    state = fold(events)

    item = state.items["t1"]
    # first create wins; the duplicate must not overwrite title/owner
    assert item.title == "First"
    assert item.owner == "mac"
    assert len(item.history) == 2
    dup_entry = item.history[1]
    assert "no-op" in dup_entry


# --- determinism / purity ----------------------------------------------------


def test_fold_is_deterministic_and_does_not_mutate_input():
    events = [
        _ev(0, "1.0.mac", "mac", "item.create", {"id": "t1", "title": "T"}),
        _ev(1, "2.0.mac", "mac", "item.claim", {"id": "t1"}),
        _ev(2, "3.0.pi", "pi", "item.claim", {"id": "t1"}),
        _ev(3, "4.0.mac", "mac", "item.complete", {"id": "t1"}),
        _ev(4, "5.0.mac", "mac", "spend.record", {"cents": 42, "ref": "x"}),
        _ev(5, "6.0.pi", "pi", "item.claim", {"id": "ghost"}),
    ]
    before = copy.deepcopy(events)

    state1 = fold(events)
    state2 = fold(events)

    assert state1 == state2
    assert events == before  # list contents/order untouched
    assert events is not before  # sanity: deepcopy actually produced a separate list


def test_fold_does_not_mutate_or_reorder_out_of_order_input():
    """fold() must trust caller-provided order and never re-sort."""
    events = [
        _ev(0, "5.0.mac", "mac", "item.create", {"id": "t1", "title": "T"}),
        _ev(1, "1.0.mac", "mac", "item.claim", {"id": "t1"}),
    ]
    original_order = [e.hlc for e in events]

    fold(events)

    assert [e.hlc for e in events] == original_order


# --- poison payloads (final-review wave: fold() must never raise) -----------


def test_item_claim_missing_id_routes_to_orphan_with_dash():
    """An item-type event with no 'id' key at all used to KeyError inside
    `_get_item_or_orphan` -- it must now be routed to orphans, rendered
    with '-' since there is no id to show."""
    events = [_ev(0, "1.0.mac", "mac", "item.claim", {})]

    state = fold(events)

    assert state.items == {}
    assert state.orphans == ["1.0.mac item.claim -"]


def test_item_create_missing_id_routes_to_orphan_not_raise():
    """item.create's own KeyError on payload["id"] is the other empirically
    confirmed crash path -- must become an orphan, not fabricate an item."""
    events = [_ev(0, "1.0.mac", "mac", "item.create", {"title": "No id here"})]

    state = fold(events)

    assert state.items == {}
    assert state.orphans == ["1.0.mac item.create -"]


def test_item_event_non_string_id_routes_to_orphan():
    events = [_ev(0, "1.0.mac", "mac", "item.claim", {"id": 12345})]

    state = fold(events)

    assert state.items == {}
    assert state.orphans == ["1.0.mac item.claim -"]


def test_item_event_empty_string_id_routes_to_orphan():
    events = [_ev(0, "1.0.mac", "mac", "item.claim", {"id": ""})]

    state = fold(events)

    assert state.items == {}
    assert state.orphans == ["1.0.mac item.claim -"]


def test_poison_item_event_does_not_affect_other_events():
    """A single malformed event in the middle of the list must not disturb
    fold()'s handling of the legitimate events around it."""
    events = [
        _ev(0, "1.0.mac", "mac", "item.create", {"id": "t1", "title": "Real"}),
        _ev(1, "2.0.pi", "pi", "item.claim", {}),  # poison: no id
        _ev(2, "3.0.mac", "mac", "item.claim", {"id": "t1"}),
    ]

    state = fold(events)

    assert state.items["t1"].status == "claimed"
    assert state.items["t1"].owner == "mac"
    assert state.orphans == ["2.0.pi item.claim -"]


def test_spend_missing_cents_skipped_as_orphan():
    events = [_ev(0, "1.0.mac", "mac", "spend.record", {"ref": "no-cents"})]

    state = fold(events)

    assert state.spend == {}
    assert state.orphans == ["1.0.mac spend.record invalid"]


def test_spend_non_int_cents_skipped_as_orphan():
    events = [_ev(0, "1.0.mac", "mac", "spend.record", {"cents": "500"})]

    state = fold(events)

    assert state.spend == {}
    assert state.orphans == ["1.0.mac spend.record invalid"]


def test_spend_bool_cents_skipped_as_orphan():
    """bool is an int subclass in Python -- True/False must never silently
    fold into spend as 1/0."""
    events = [_ev(0, "1.0.mac", "mac", "spend.record", {"cents": True})]

    state = fold(events)

    assert state.spend == {}
    assert state.orphans == ["1.0.mac spend.record invalid"]


def test_spend_negative_cents_skipped_as_orphan():
    """Substrate-side conservation floor: spend only ever accumulates."""
    events = [_ev(0, "1.0.mac", "mac", "spend.record", {"cents": -100})]

    state = fold(events)

    assert state.spend == {}
    assert state.orphans == ["1.0.mac spend.record invalid"]


def test_poison_spend_does_not_affect_other_actors_balances():
    events = [
        _ev(0, "1.0.mac", "mac", "spend.record", {"cents": 500}),
        _ev(1, "2.0.pi", "pi", "spend.record", {"cents": -1}),  # poison
        _ev(2, "3.0.mac", "mac", "spend.record", {"cents": 100}),
    ]

    state = fold(events)

    assert state.spend == {"mac": 600}
    assert state.orphans == ["2.0.pi spend.record invalid"]


def test_fold_never_raises_on_grab_bag_of_poison_payloads():
    """Comprehensive smoke test: malformed payloads across every
    fold-handled event type must never raise -- fold() always returns a
    LedgerState, routing what it can't process to orphans."""
    events = [
        _ev(0, "1.0.a", "a", "item.create", {}),
        _ev(1, "2.0.a", "a", "item.claim", {"id": None}),
        _ev(2, "3.0.a", "a", "item.release", {"id": 42}),
        _ev(3, "4.0.a", "a", "item.complete", {}),
        _ev(4, "5.0.a", "a", "item.verify", {"id": ""}),
        _ev(5, "6.0.a", "a", "item.dismiss", {}),
        _ev(6, "7.0.a", "a", "owner.set", {"id": "ghost"}),
        _ev(7, "8.0.a", "a", "spend.record", {}),
        _ev(8, "9.0.a", "a", "spend.record", {"cents": None}),
        _ev(9, "10.0.a", "a", "spend.record", {"cents": -5}),
        _ev(10, "11.0.a", "a", "spend.record", {"cents": False}),
    ]

    state = fold(events)  # must not raise

    assert state.items == {}
    assert state.spend == {}
    assert len(state.orphans) == len(events)


def test_list_order_is_authoritative_even_when_hlc_order_disagrees():
    """Pins "input order authoritative" per the fold() contract: the caller
    (log.read_events) is responsible for hlc-sorting before fold() sees the
    list, so fold() must never re-derive winner order from the hlc strings
    themselves. Here the event that is FIRST IN THE LIST carries the LATER
    hlc, and the event that is SECOND IN THE LIST carries the EARLIER hlc --
    the opposite of what a correctly hlc-sorted list would look like. If
    fold() ever re-sorts internally (e.g. `sorted(events, key=...hlc)`),
    this test flips the winner and fails.
    """
    events = [
        _ev(0, "1.0.mac", "mac", "item.create", {"id": "t1", "title": "Order"}),
        _ev(1, "9.0.pi", "pi", "item.claim", {"id": "t1"}),  # list-first claim, LATER hlc
        _ev(2, "2.0.mac", "mac", "item.claim", {"id": "t1"}),  # list-second claim, EARLIER hlc
    ]

    state = fold(events)

    item = state.items["t1"]
    # list-first (pi) must win despite its hlc ("9.0.pi") being
    # chronologically later than mac's ("2.0.mac").
    assert item.owner == "pi"
    assert item.status == "claimed"
    loser_entries = [h for h in item.history if "item.claim" in h and "mac" in h]
    assert len(loser_entries) == 1
    assert "no-op" in loser_entries[0]
