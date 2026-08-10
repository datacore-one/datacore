"""Tests for briefing.actions -- turning briefing items into ledger items.

The core guarantee this module exists to provide: "dismissed means gone
forever" is mechanical, not a convention someone has to remember. Two
mechanisms combine to make that true:

- `item_id(text)` is a pure function of NORMALIZED text (lowercase,
  whitespace runs collapsed, stripped) -- the same underlying briefing
  item, phrased slightly differently across two runs, always maps to the
  same id.
- `materialize` folds the space's ledger ONCE per call and skips any item
  whose id already appears in `state.items` with ANY status -- including
  "dismissed". So once a human dismisses an item, no future briefing run
  (even one that re-derives the exact same item text) can resurrect it:
  the id is already known, `item.create` is never re-appended, and
  `guarded_append` doesn't get a chance to short-circuit anything -- the
  skip happens before we'd even try.

`materialize` never lets one item's `PolicyError` (a side-effect item
whose `approval_ref` is missing/invalid) abort the whole call: it collects
the failure into `MaterializeResult.blocked` and keeps going, so the rest
of that day's briefing still gets materialized even if one item needed a
grant nobody has produced yet.

`act` is deliberately thin -- `claim`/`complete`/`dismiss` just append the
matching `item.*` event via a plain `EventLog` (no policy gate: only
`item.create` is ever gated, per `ledger.policy`).
"""

from __future__ import annotations

import pytest

from briefing.actions import MaterializeResult, act, item_id, materialize
from ledger.fold import fold
from ledger.log import EventLog, read_events
from ledger.policy import Policy


ACTOR = "worker"


@pytest.fixture(autouse=True)
def _no_signing(monkeypatch):
    # Hermetic: default (unsigned) EventLog must never touch real key
    # material, regardless of what's in the ambient environment.
    monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)


def _grant(space_dir, approver, item_id):
    """Append an `approval.grant` for `item_id`, signed by `approver`, via
    a plain EventLog -- mirrors how ledger.policy tests seed grants.
    """
    grant_log = EventLog(space_dir, approver, sign=False)
    return grant_log.append("approval.grant", {"item": item_id})


# --- item_id ---------------------------------------------------------------


def test_item_id_same_text_same_id():
    assert item_id("Buy milk") == item_id("Buy milk")


def test_item_id_case_insensitive():
    assert item_id("Buy milk") == item_id("buy milk")
    assert item_id("BUY MILK") == item_id("buy milk")


def test_item_id_whitespace_collapsed():
    assert item_id("Buy   milk") == item_id("Buy milk")
    assert item_id("Buy\tmilk\n") == item_id("Buy milk")
    assert item_id("  Buy milk  ") == item_id("Buy milk")


def test_item_id_combined_case_and_whitespace_variants_all_equal():
    variants = ["Buy milk", "buy   milk", "  BUY MILK  ", "Buy\tMILK\n\n"]
    ids = {item_id(v) for v in variants}
    assert len(ids) == 1


def test_item_id_different_text_different_id():
    assert item_id("Buy milk") != item_id("Buy bread")


def test_item_id_is_sha256_hex16():
    tid = item_id("Buy milk")
    assert isinstance(tid, str)
    assert len(tid) == 16
    assert all(c in "0123456789abcdef" for c in tid)


# --- materialize: creation ---------------------------------------------------


def test_materialize_creates_item_for_each_item(tmp_path):
    space = tmp_path / "space"
    items = [{"text": "Buy milk"}, {"text": "Call plumber"}]

    result = materialize(items, space, ACTOR)

    assert isinstance(result, MaterializeResult)
    assert len(result.created) == 2
    assert result.skipped == []
    assert result.blocked == []

    state = fold(read_events(space))
    assert len(state.items) == 2
    ids = {item_id("Buy milk"), item_id("Call plumber")}
    assert set(state.items.keys()) == ids


def test_materialize_payload_shape(tmp_path):
    space = tmp_path / "space"
    items = [{"text": "Buy milk"}]

    result = materialize(items, space, ACTOR)

    event = result.created[0]
    assert event.type == "item.create"
    assert event.payload["id"] == item_id("Buy milk")
    assert event.payload["title"] == "Buy milk"  # original, UNnormalized
    assert event.payload["effects"] == []
    # A plain item's payload is EXACTLY these three keys -- no stray
    # "approval_ref" (or anything else) leaking in when the item never
    # supplied one.
    assert set(event.payload.keys()) == {"id", "title", "effects"}
    assert "approval_ref" not in event.payload


def test_materialize_preserves_original_unnormalized_title(tmp_path):
    """title stored is the ORIGINAL text, not the normalized form used for
    the id -- normalization is only for the id/dedupe key."""
    space = tmp_path / "space"
    items = [{"text": "  Buy   Milk  "}]

    result = materialize(items, space, ACTOR)

    assert result.created[0].payload["title"] == "  Buy   Milk  "


def test_materialize_passes_through_effects(tmp_path):
    """Effects not in cosign_effects still pass through verbatim, as long
    as they're registered in the policy's known_effects (the closed
    effects vocabulary -- ledger.policy's final-review-wave amendment
    makes an unregistered effect a hard PolicyError). The real tracked
    default policy only knows the three cosign effects, so this test
    supplies an explicit policy naming "harmless.op" as a known,
    non-cosign effect -- it must not require a grant."""
    space = tmp_path / "space"
    policy = Policy(
        approver="human",
        cosign_effects=frozenset(),
        known_effects=frozenset({"harmless.op"}),
    )
    items = [{"text": "harmless task", "effects": ["harmless.op"]}]

    result = materialize(items, space, ACTOR, policy=policy)

    assert result.created[0].payload["effects"] == ["harmless.op"]


def test_materialize_defaults_missing_effects_to_empty_list(tmp_path):
    space = tmp_path / "space"
    items = [{"text": "no effects key here"}]

    result = materialize(items, space, ACTOR)

    assert result.created[0].payload["effects"] == []


def test_materialize_empty_items_is_a_no_op(tmp_path):
    space = tmp_path / "space"

    result = materialize([], space, ACTOR)

    assert result.created == []
    assert result.skipped == []
    assert result.blocked == []
    assert fold(read_events(space)).items == {}


# --- materialize: idempotence (re-materialize same items) -------------------


def test_rematerialize_same_items_creates_nothing(tmp_path):
    space = tmp_path / "space"
    items = [{"text": "Buy milk"}, {"text": "Call plumber"}]

    first = materialize(items, space, ACTOR)
    assert len(first.created) == 2

    second = materialize(items, space, ACTOR)

    assert second.created == []
    assert set(second.skipped) == {item_id("Buy milk"), item_id("Call plumber")}
    assert second.blocked == []

    # And the ledger itself only ever saw one item.create per id.
    events = read_events(space)
    creates = [e for e in events if e.type == "item.create"]
    assert len(creates) == 2


def test_rematerialize_with_reworded_but_equivalent_text_still_skips(tmp_path):
    """Same underlying meaning (case/whitespace variant) -> same item_id
    -> still skipped on a second materialize call, even though the exact
    string differs."""
    space = tmp_path / "space"
    materialize([{"text": "Buy milk"}], space, ACTOR)

    second = materialize([{"text": "  BUY   milk  "}], space, ACTOR)

    assert second.created == []
    assert second.skipped == [item_id("Buy milk")]


# --- materialize: THE guarantee -- dismiss then re-materialize --------------


def test_dismiss_then_rematerialize_creates_nothing(tmp_path):
    """THE acceptance guarantee: once an item is dismissed, no future
    materialize call (even a fresh fold reading the dismissal event) can
    ever bring it back."""
    space = tmp_path / "space"
    items = [{"text": "Buy milk"}]

    first = materialize(items, space, ACTOR)
    tid = first.created[0].payload["id"]

    act(space, tid, "dismiss", ACTOR)

    # Verify at the fold level (not just via materialize's own bookkeeping)
    # that the item really is dismissed before re-materializing.
    state_before = fold(read_events(space))
    assert state_before.items[tid].status == "dismissed"

    second = materialize(items, space, ACTOR)

    assert second.created == []
    assert second.skipped == [tid]

    # Fresh fold, post re-materialize: still dismissed, still exactly one
    # item, no phantom resurrection.
    state_after = fold(read_events(space))
    assert len(state_after.items) == 1
    assert state_after.items[tid].status == "dismissed"


# --- materialize: in-call duplicate normalization ---------------------------


def test_in_call_duplicate_normalization_one_created_one_skipped(tmp_path):
    space = tmp_path / "space"
    items = [{"text": "Fix Bug"}, {"text": "FIX   bug"}]

    result = materialize(items, space, ACTOR)

    assert len(result.created) == 1
    assert result.skipped == [item_id("Fix Bug")]
    assert result.blocked == []

    # The up-front fold couldn't have known about this -- it's genuinely
    # an in-loop dedupe -- but the end state is still exactly one item.
    state = fold(read_events(space))
    assert len(state.items) == 1


def test_in_call_duplicate_normalization_first_wins_title(tmp_path):
    space = tmp_path / "space"
    items = [{"text": "Fix Bug"}, {"text": "FIX   bug"}]

    result = materialize(items, space, ACTOR)

    assert result.created[0].payload["title"] == "Fix Bug"


# --- materialize: policy gate -----------------------------------------------


def test_side_effect_item_without_grant_is_blocked_others_still_created(tmp_path):
    space = tmp_path / "space"
    policy = Policy(approver="human", cosign_effects=frozenset({"email.send"}))
    items = [
        {"text": "Email the client", "effects": ["email.send"]},
        {"text": "Buy milk"},
    ]

    result = materialize(items, space, ACTOR, policy=policy)

    assert len(result.blocked) == 1
    blocked_text, blocked_error = result.blocked[0]
    assert blocked_text == "Email the client"
    assert isinstance(blocked_error, str) and blocked_error

    # The OTHER item still got created -- one blocked item does not stop
    # the rest of the call.
    assert len(result.created) == 1
    assert result.created[0].payload["title"] == "Buy milk"
    assert result.skipped == []

    # And the blocked item never touched the log at all.
    state = fold(read_events(space))
    assert len(state.items) == 1
    assert item_id("Email the client") not in state.items


def test_blocked_item_does_not_prevent_later_items_from_creating(tmp_path):
    """Same as above but with more items after the blocked one, to make
    sure the loop really continues past it rather than stopping early."""
    space = tmp_path / "space"
    policy = Policy(approver="human", cosign_effects=frozenset({"email.send"}))
    items = [
        {"text": "Email the client", "effects": ["email.send"]},
        {"text": "Buy milk"},
        {"text": "Call plumber"},
        {"text": "Water the plants"},
    ]

    result = materialize(items, space, ACTOR, policy=policy)

    assert len(result.blocked) == 1
    assert len(result.created) == 3
    created_titles = {e.payload["title"] for e in result.created}
    assert created_titles == {"Buy milk", "Call plumber", "Water the plants"}


def test_side_effect_item_with_valid_grant_is_created(tmp_path):
    space = tmp_path / "space"
    policy = Policy(approver="human", cosign_effects=frozenset({"email.send"}))
    text = "Email the client"
    tid = item_id(text)
    grant = _grant(space, "human", tid)

    items = [{"text": text, "effects": ["email.send"], "approval_ref": grant.hash}]

    result = materialize(items, space, ACTOR, policy=policy)

    assert result.blocked == []
    assert result.skipped == []
    assert len(result.created) == 1
    assert result.created[0].payload["id"] == tid

    state = fold(read_events(space))
    assert state.items[tid].status == "created"


def test_multiple_blocked_items_all_collected(tmp_path):
    space = tmp_path / "space"
    policy = Policy(approver="human", cosign_effects=frozenset({"email.send", "payment"}))
    items = [
        {"text": "Email the client", "effects": ["email.send"]},
        {"text": "Pay the invoice", "effects": ["payment"]},
    ]

    result = materialize(items, space, ACTOR, policy=policy)

    assert result.created == []
    assert len(result.blocked) == 2
    blocked_texts = {text for text, _err in result.blocked}
    assert blocked_texts == {"Email the client", "Pay the invoice"}


def test_materialize_default_policy_used_when_none_passed(tmp_path):
    """No policy kwarg -> guarded_append's own default (load_policy()) --
    a plain item with no effects must still pass straight through."""
    space = tmp_path / "space"
    items = [{"text": "Buy milk"}]

    result = materialize(items, space, ACTOR)

    assert len(result.created) == 1
    assert result.blocked == []


def test_materialize_default_policy_actually_consulted_not_bypassed(tmp_path):
    """No `policy=` kwarg at all -- and yet a side-effect item with no
    grant STILL lands in blocked. This proves `materialize` really
    threads `policy=None` through to `guarded_append` (which then
    consults its own `load_policy()` default), rather than materialize
    silently skipping the gate whenever it isn't handed a Policy object
    explicitly. `email.send` is in the default policy's `cosign_effects`
    (`ledger.policy.DEFAULT_COSIGN_EFFECTS`, and the tracked
    `.datacore/config/approvals_policy.yaml` agrees), so this item requires
    cosign with or without a policy file present.
    """
    space = tmp_path / "space"
    items = [{"text": "Email the client", "effects": ["email.send"]}]

    result = materialize(items, space, ACTOR)

    assert result.created == []
    assert len(result.blocked) == 1
    blocked_text, _err = result.blocked[0]
    assert blocked_text == "Email the client"

    state = fold(read_events(space))
    assert item_id("Email the client") not in state.items


# --- act ---------------------------------------------------------------


def test_act_claim_appends_item_claim(tmp_path):
    # Same actor for materialize + act: a different actor's independent
    # HLC tick can legitimately tie-break lexically against a create that
    # landed in the same millisecond -- that cross-writer ordering nuance
    # belongs to ledger.hlc/ledger.log, not this test. Using one actor's
    # own monotonic chain keeps this test's ordering assertion unrelated
    # to that.
    space = tmp_path / "space"
    tid = materialize([{"text": "Buy milk"}], space, ACTOR).created[0].payload["id"]

    event = act(space, tid, "claim", ACTOR)

    assert event.type == "item.claim"
    assert event.payload == {"id": tid}
    assert event.actor == ACTOR

    state = fold(read_events(space))
    assert state.items[tid].status == "claimed"
    assert state.items[tid].owner == ACTOR


def test_act_complete_appends_item_complete(tmp_path):
    space = tmp_path / "space"
    tid = materialize([{"text": "Buy milk"}], space, ACTOR).created[0].payload["id"]
    act(space, tid, "claim", ACTOR)

    event = act(space, tid, "complete", ACTOR)

    assert event.type == "item.complete"
    state = fold(read_events(space))
    assert state.items[tid].status == "completed"


def test_act_dismiss_appends_item_dismiss(tmp_path):
    space = tmp_path / "space"
    tid = materialize([{"text": "Buy milk"}], space, ACTOR).created[0].payload["id"]

    event = act(space, tid, "dismiss", ACTOR)

    assert event.type == "item.dismiss"
    state = fold(read_events(space))
    assert state.items[tid].status == "dismissed"


def test_act_round_trips_full_lifecycle_via_fold(tmp_path):
    space = tmp_path / "space"
    tid = materialize([{"text": "Buy milk"}], space, ACTOR).created[0].payload["id"]

    act(space, tid, "claim", ACTOR)
    act(space, tid, "complete", ACTOR)

    state = fold(read_events(space))
    assert state.items[tid].status == "completed"
    assert state.items[tid].owner == ACTOR
    # History shows the applied transitions in order.
    outcomes = [line.split(": ", 1)[1] for line in state.items[tid].history]
    assert outcomes == ["applied", "applied", "applied"]


def test_act_unknown_action_raises_value_error(tmp_path):
    space = tmp_path / "space"
    tid = materialize([{"text": "Buy milk"}], space, ACTOR).created[0].payload["id"]

    with pytest.raises(ValueError):
        act(space, tid, "obliterate", ACTOR)

    # Nothing appended for the bad action.
    state = fold(read_events(space))
    assert state.items[tid].status == "created"


# --- Phase 5 close: end-to-end acceptance roll-up ---------------------------
#
# These two tests exercise the full loop this phase built, in the shape a
# real caller (a briefing pipeline, then a human dismissing/approving via
# Telegram) would actually drive it -- not just the individual mechanisms
# already covered above in isolation.


def test_acceptance_full_cycle_dismiss_then_rematerialize_with_new_item(tmp_path):
    """Full cycle: materialize [A, B] -> act(dismiss, A) -> re-materialize
    [A, B, C] -> exactly one item.create (C). A is absent from every later
    fold's non-dismissed items -- not just immediately after the dismiss,
    but after the SECOND materialize call too, and after a third call that
    repeats the same three items again (simulating yet another briefing
    run). This is the never-resurface guarantee end to end, not just the
    single-call version already covered by
    `test_dismiss_then_rematerialize_creates_nothing` above.
    """
    space = tmp_path / "space"
    item_a = {"text": "Reply to the investor's email"}
    item_b = {"text": "Review PR #42"}
    item_c = {"text": "Schedule the board meeting"}

    first = materialize([item_a, item_b], space, ACTOR)
    assert len(first.created) == 2
    tid_a = item_id(item_a["text"])
    tid_b = item_id(item_b["text"])

    act(space, tid_a, "dismiss", ACTOR)

    state_after_dismiss = fold(read_events(space))
    assert state_after_dismiss.items[tid_a].status == "dismissed"

    second = materialize([item_a, item_b, item_c], space, ACTOR)

    tid_c = item_id(item_c["text"])
    assert len(second.created) == 1
    assert second.created[0].payload["id"] == tid_c
    assert second.created[0].payload["title"] == item_c["text"]
    assert set(second.skipped) == {tid_a, tid_b}
    assert second.blocked == []

    def non_dismissed_ids(state):
        return {tid for tid, item in state.items.items() if item.status != "dismissed"}

    state_after_second = fold(read_events(space))
    assert len(state_after_second.items) == 3
    assert tid_a not in non_dismissed_ids(state_after_second)
    assert state_after_second.items[tid_a].status == "dismissed"
    assert non_dismissed_ids(state_after_second) == {tid_b, tid_c}

    # A third materialize call, repeating all three items again (as a
    # fresh briefing run naturally would), must still never resurrect A --
    # and must not re-create B or C either, since both are already known.
    third = materialize([item_a, item_b, item_c], space, ACTOR)
    assert third.created == []
    assert set(third.skipped) == {tid_a, tid_b, tid_c}

    state_final = fold(read_events(space))
    assert len(state_final.items) == 3
    assert tid_a not in non_dismissed_ids(state_final)
    assert state_final.items[tid_a].status == "dismissed"

    creates = [e for e in read_events(space) if e.type == "item.create"]
    assert len(creates) == 3  # A, B, C -- exactly one create each, ever.


def test_acceptance_side_effect_cycle_blocked_then_granted_then_created(tmp_path):
    """Side-effect cycle: materialize an item with a cosign-gated effect ->
    blocked (no grant exists yet). A human approver appends
    `approval.grant` for that item's id via a plain `EventLog` (the grant
    itself is never policy-gated -- only `item.create` is, per
    `ledger.policy`). Re-materializing the SAME item, now carrying
    `approval_ref`, succeeds.

    The materializer and the approver are DIFFERENT actors ("agent" vs
    "human") -- deliberately, to exercise the Task 5.2b cross-actor HLC
    causal floor (see `ledger.log` module docstring, CROSS-ACTOR ORDERING):
    the grant (written to `human.jsonl`) must sort before the create it
    authorizes (written to `agent.jsonl`) once `guarded_append` scans
    `read_events` for it, even though they are two different writers'
    files. Before 5.2b's append-causal floor fix, a same-millisecond
    cross-actor race could tie-break on actor name alone; distinct actors
    on either side of a grant-then-create dependency is exactly the shape
    that fix targets, so this is now safe to assert without a race.
    """
    space = tmp_path / "space"
    policy = Policy(approver="human", cosign_effects=frozenset({"email.send"}))
    materializer_actor = "agent"
    text = "Email the client the signed contract"
    tid = item_id(text)
    item = {"text": text, "effects": ["email.send"]}

    first = materialize([item], space, materializer_actor, policy=policy)

    assert first.created == []
    assert len(first.blocked) == 1
    blocked_text, blocked_error = first.blocked[0]
    assert blocked_text == text
    assert isinstance(blocked_error, str) and blocked_error

    state_before_grant = fold(read_events(space))
    assert tid not in state_before_grant.items

    grant = _grant(space, "human", tid)

    item_with_ref = {**item, "approval_ref": grant.hash}
    second = materialize([item_with_ref], space, materializer_actor, policy=policy)

    assert second.blocked == []
    assert second.skipped == []
    assert len(second.created) == 1
    assert second.created[0].payload["id"] == tid
    assert second.created[0].payload["approval_ref"] == grant.hash

    state_after = fold(read_events(space))
    assert state_after.items[tid].status == "created"
