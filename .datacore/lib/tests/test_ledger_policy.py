"""Tests for ledger.policy - the co-sign gate.

Covers: load_policy (default / file / invalid shape), requires_cosign
(the item.create + effects-intersection matrix), and guarded_append (the
actual enforcement point -- reject-before-append, valid-grant acceptance,
and pass-through for non-cosign events).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _register_test_writers(tmp_path_factory, monkeypatch):
    """Since 2026-09-06 an item.create by an unregistered writer is refused
    before any other check (claim_gate). These tests use short-lived writer
    names; register them for the duration of each test."""
    import actor_identity
    p = tmp_path_factory.mktemp("reg") / "principals.yaml"
    p.write_text("principals:\n  worker: {kind: agent, writes_as: [worker, w, agent]}\n  human: {kind: human, writes_as: [human, approver, mac, test]}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", p)

from ledger.events import EVENT_TYPES
from ledger.log import EventLog, read_events
from ledger.policy import Policy, PolicyError, guarded_append, load_policy, requires_cosign

DEFAULT_EFFECTS = frozenset({"email.send", "payment", "prod.deploy"})


def _mk_log(tmp_path, actor, space="space"):
    return EventLog(
        tmp_path / space,
        actor,
        keys_dir=tmp_path / "keys",
        registry_path=tmp_path / "registry.yaml",
        sign=False,
    )


# --- load_policy --------------------------------------------------------


def test_load_policy_default_when_file_missing(tmp_path):
    policy = load_policy(tmp_path / "does-not-exist.yaml")
    assert policy.approver == "human"
    assert policy.cosign_effects == DEFAULT_EFFECTS
    assert isinstance(policy.cosign_effects, frozenset)


def test_load_policy_from_file(tmp_path):
    path = tmp_path / "approvals_policy.yaml"
    path.write_text(
        "version: 1\n"
        "approver: gregor\n"
        "cosign_effects:\n"
        "  - email.send\n"
        "  - custom.effect\n"
    )
    policy = load_policy(path)
    assert policy.approver == "gregor"
    assert policy.cosign_effects == frozenset({"email.send", "custom.effect"})


def test_load_policy_missing_version_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("approver: human\ncosign_effects: [email.send]\n")
    with pytest.raises(PolicyError):
        load_policy(path)


def test_load_policy_wrong_version_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("version: 2\napprover: human\ncosign_effects: [email.send]\n")
    with pytest.raises(PolicyError):
        load_policy(path)


def test_load_policy_missing_approver_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("version: 1\ncosign_effects: [email.send]\n")
    with pytest.raises(PolicyError):
        load_policy(path)


def test_load_policy_bad_cosign_effects_type_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("version: 1\napprover: human\ncosign_effects: not-a-list\n")
    with pytest.raises(PolicyError):
        load_policy(path)


def test_load_policy_non_mapping_root_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("- just\n- a\n- list\n")
    with pytest.raises(PolicyError):
        load_policy(path)


def test_load_policy_reports_multiple_problems_together(tmp_path):
    """Invalid shape -> PolicyError listing problems, not just the first one."""
    path = tmp_path / "bad.yaml"
    path.write_text("version: 2\ncosign_effects: not-a-list\n")
    with pytest.raises(PolicyError) as exc_info:
        load_policy(path)
    msg = str(exc_info.value)
    assert "version" in msg
    assert "approver" in msg
    assert "cosign_effects" in msg


# --- known_effects (closed effects vocabulary, final-review wave) ---------


def test_load_policy_known_effects_absent_defaults_to_none(tmp_path):
    """No known_effects key at all -> Policy.known_effects is None, and
    effective_known_effects falls back to cosign_effects."""
    path = tmp_path / "policy.yaml"
    path.write_text("version: 1\napprover: human\ncosign_effects: [email.send]\n")
    policy = load_policy(path)
    assert policy.known_effects is None
    assert policy.effective_known_effects == frozenset({"email.send"})


def test_load_policy_known_effects_from_file(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(
        "version: 1\n"
        "approver: human\n"
        "cosign_effects: [email.send]\n"
        "known_effects:\n"
        "  - email.send\n"
        "  - harmless.op\n"
    )
    policy = load_policy(path)
    assert policy.known_effects == frozenset({"email.send", "harmless.op"})
    assert policy.effective_known_effects == policy.known_effects


def test_load_policy_bad_known_effects_type_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "version: 1\napprover: human\ncosign_effects: [email.send]\nknown_effects: not-a-list\n"
    )
    with pytest.raises(PolicyError):
        load_policy(path)


def test_default_policy_known_effects_is_none_falls_back_to_cosign_effects():
    """A bare Policy(...) construction (every pre-existing caller/test)
    leaves known_effects at its default of None -- effective_known_effects
    must still resolve to cosign_effects, not an empty set."""
    policy = Policy(approver="human", cosign_effects=DEFAULT_EFFECTS)
    assert policy.known_effects is None
    assert policy.effective_known_effects == DEFAULT_EFFECTS


# --- requires_cosign -----------------------------------------------------


def _policy():
    return Policy(approver="human", cosign_effects=DEFAULT_EFFECTS)


def test_requires_cosign_true_on_effect_match():
    assert requires_cosign(_policy(), "item.create", {"id": "t1", "effects": ["email.send"]})


def test_requires_cosign_false_no_effects():
    assert not requires_cosign(_policy(), "item.create", {"id": "t1"})


def test_requires_cosign_false_empty_effects_list():
    assert not requires_cosign(_policy(), "item.create", {"id": "t1", "effects": []})


def test_requires_cosign_false_non_matching_effects():
    assert not requires_cosign(_policy(), "item.create", {"id": "t1", "effects": ["harmless.op"]})


@pytest.mark.parametrize("event_type", ["item.claim", "item.complete", "item.release", "item.verify"])
def test_requires_cosign_false_for_non_create_types_even_with_matching_effects(event_type):
    assert not requires_cosign(_policy(), event_type, {"id": "t1", "effects": ["payment"]})


def test_requires_cosign_true_on_partial_intersection():
    assert requires_cosign(
        _policy(), "item.create", {"id": "t1", "effects": ["harmless.op", "prod.deploy"]}
    )


# --- guarded_append: rejection paths --------------------------------------


def test_guarded_append_rejects_missing_approval_ref(tmp_path):
    log = _mk_log(tmp_path, "worker")
    payload = {"id": "t1", "title": "send an email", "effects": ["email.send"]}

    with pytest.raises(PolicyError):
        guarded_append(log, "item.create", payload)

    assert not (tmp_path / "space" / ".datacore" / "events" / "worker.jsonl").exists()


def test_guarded_append_rejects_nonexistent_ref(tmp_path):
    log = _mk_log(tmp_path, "worker")
    payload = {
        "id": "t1",
        "title": "send an email",
        "effects": ["email.send"],
        "approval_ref": "deadbeef" * 8,
    }

    with pytest.raises(PolicyError):
        guarded_append(log, "item.create", payload)


def test_guarded_append_rejects_wrong_actor_grant(tmp_path):
    # Grant exists but was made by "not-human", not the policy's approver.
    grant_log = _mk_log(tmp_path, "not-human")
    grant = grant_log.append("approval.grant", {"item": "t1"})

    log = _mk_log(tmp_path, "worker")
    payload = {
        "id": "t1",
        "title": "send an email",
        "effects": ["email.send"],
        "approval_ref": grant.hash,
    }

    with pytest.raises(PolicyError):
        guarded_append(log, "item.create", payload)


def test_guarded_append_rejects_wrong_item_grant(tmp_path):
    grant_log = _mk_log(tmp_path, "human")
    grant = grant_log.append("approval.grant", {"item": "some-other-item"})

    log = _mk_log(tmp_path, "worker")
    payload = {
        "id": "t1",
        "title": "send an email",
        "effects": ["email.send"],
        "approval_ref": grant.hash,
    }

    with pytest.raises(PolicyError):
        guarded_append(log, "item.create", payload)


def test_guarded_append_rejects_ref_to_non_grant_event(tmp_path):
    other_log = _mk_log(tmp_path, "human")
    other = other_log.append("item.create", {"id": "unrelated"})

    log = _mk_log(tmp_path, "worker")
    payload = {
        "id": "t1",
        "title": "send an email",
        "effects": ["email.send"],
        "approval_ref": other.hash,
    }

    with pytest.raises(PolicyError):
        guarded_append(log, "item.create", payload)


def test_guarded_append_rejected_event_leaves_log_byte_identical(tmp_path):
    """A rejected event never touches the log file -- validate BEFORE append."""
    log = _mk_log(tmp_path, "worker")
    # Seed the log with one legitimate event first.
    log.append("item.create", {"id": "seed"})

    log_path = log.path
    before = log_path.read_bytes()

    payload = {"id": "t1", "title": "send an email", "effects": ["email.send"]}
    with pytest.raises(PolicyError):
        guarded_append(log, "item.create", payload)

    after = log_path.read_bytes()
    assert before == after


def test_guarded_append_rejects_gated_create_missing_id(tmp_path):
    """The grant is legitimate (has a real item binding) -- but the create
    itself carries no 'id' at all. Must still be rejected, and specifically
    for the missing id, not silently treated as a match."""
    grant_log = _mk_log(tmp_path, "human")
    grant = grant_log.append("approval.grant", {"item": "t1"})

    log = _mk_log(tmp_path, "worker")
    payload = {
        "title": "send an email",
        "effects": ["email.send"],
        "approval_ref": grant.hash,
        # no "id" key at all
    }

    with pytest.raises(PolicyError) as exc_info:
        guarded_append(log, "item.create", payload)
    assert "non-empty id" in str(exc_info.value)


def test_guarded_append_rejects_grant_missing_item_binding(tmp_path):
    """The create carries a perfectly good id -- but the referenced grant
    has no 'item' key at all. Must still be rejected: a grant with no item
    binding can never validate any create."""
    grant_log = _mk_log(tmp_path, "human")
    grant = grant_log.append("approval.grant", {})  # no "item" key

    log = _mk_log(tmp_path, "worker")
    payload = {
        "id": "t1",
        "effects": ["email.send"],
        "approval_ref": grant.hash,
    }

    with pytest.raises(PolicyError) as exc_info:
        guarded_append(log, "item.create", payload)
    assert "item binding" in str(exc_info.value)


def test_guarded_append_rejects_double_vacuous_bypass(tmp_path):
    """THE critical regression: a grant with no 'item' key AND a create
    with no 'id' key must NOT be treated as a match just because both
    sides default to None. This is the exact vacuous-comparison bypass
    the gate must never allow."""
    grant_log = _mk_log(tmp_path, "human")
    grant = grant_log.append("approval.grant", {})  # no "item" key

    log = _mk_log(tmp_path, "worker")
    payload = {
        "effects": ["email.send"],
        "approval_ref": grant.hash,
        # no "id" key at all
    }

    with pytest.raises(PolicyError):
        guarded_append(log, "item.create", payload)

    # and, as ever, nothing was appended
    assert not (tmp_path / "space" / ".datacore" / "events" / "worker.jsonl").exists()


def test_guarded_append_blocks_replay_of_same_approval_ref(tmp_path):
    """One grant authorizes creating its item exactly once. A second gated
    create against the same id, reusing the same (still-valid) grant, must
    be rejected as a replay -- not silently re-validated."""
    grant_log = _mk_log(tmp_path, "human")
    grant = grant_log.append("approval.grant", {"item": "t1"})

    log = _mk_log(tmp_path, "worker")
    payload = {
        "id": "t1",
        "effects": ["email.send"],
        "approval_ref": grant.hash,
    }

    first = guarded_append(log, "item.create", payload)
    assert first.type == "item.create"

    with pytest.raises(PolicyError) as exc_info:
        guarded_append(log, "item.create", payload)
    assert "t1" in str(exc_info.value)
    assert "already created" in str(exc_info.value)


def test_guarded_append_does_not_block_replay_for_non_cosign_creates(tmp_path):
    """The replay block is scoped to gated creates only -- an ungated
    duplicate item.create (no effects) is fold's business, not the
    policy gate's, and must pass through both times."""
    log = _mk_log(tmp_path, "worker")
    payload = {"id": "t1", "title": "harmless task"}

    first = guarded_append(log, "item.create", payload)
    second = guarded_append(log, "item.create", payload)
    assert first.type == second.type == "item.create"

    events = read_events(tmp_path / "space")
    assert len([e for e in events if e.type == "item.create"]) == 2


def test_guarded_append_rejects_non_list_effects(tmp_path):
    """A bare string 'effects' (not wrapped in a list) fails closed --
    Python would otherwise happily iterate it character-by-character."""
    log = _mk_log(tmp_path, "worker")
    payload = {"id": "t1", "effects": "email.send"}

    with pytest.raises(PolicyError) as exc_info:
        guarded_append(log, "item.create", payload)
    assert "must be a list" in str(exc_info.value)

    assert not (tmp_path / "space" / ".datacore" / "events" / "worker.jsonl").exists()


def test_guarded_append_rejects_typo_effect_not_in_known_effects(tmp_path):
    """A typo'd effect (e.g. 'emial.send') doesn't intersect cosign_effects
    either, so without the known_effects gate it would silently sail
    through ungated. Fail closed instead: reject it, naming the effect."""
    policy = Policy(approver="human", cosign_effects=frozenset({"email.send"}))
    log = _mk_log(tmp_path, "worker")
    payload = {"id": "t1", "effects": ["emial.send"]}

    with pytest.raises(PolicyError) as exc_info:
        guarded_append(log, "item.create", payload, policy=policy)
    assert "emial.send" in str(exc_info.value)
    assert not (tmp_path / "space" / ".datacore" / "events" / "worker.jsonl").exists()


def test_guarded_append_rejects_known_non_cosign_effect_when_not_registered(tmp_path):
    """With the default known_effects == cosign_effects, ANY effect
    outside that closed set is rejected -- even a perfectly legitimate,
    never-meant-to-be-gated one -- until it's explicitly registered."""
    policy = Policy(approver="human", cosign_effects=frozenset({"email.send"}))
    log = _mk_log(tmp_path, "worker")
    payload = {"id": "t1", "effects": ["harmless.op"]}

    with pytest.raises(PolicyError) as exc_info:
        guarded_append(log, "item.create", payload, policy=policy)
    assert "harmless.op" in str(exc_info.value)


def test_guarded_append_accepts_effect_registered_in_custom_known_effects(tmp_path):
    """Adding a custom known_effects entry permits a non-cosign effect
    WITHOUT requiring a grant -- it's known, just not in cosign_effects."""
    policy = Policy(
        approver="human",
        cosign_effects=frozenset({"email.send"}),
        known_effects=frozenset({"email.send", "harmless.op"}),
    )
    log = _mk_log(tmp_path, "worker")
    payload = {"id": "t1", "effects": ["harmless.op"]}

    event = guarded_append(log, "item.create", payload, policy=policy)
    assert event.type == "item.create"
    assert event.payload == payload


def test_guarded_append_scans_empty_space_without_crashing(tmp_path):
    """A space with zero events yet -- the approval scan must not blow up,
    it should just fail to find the ref and raise PolicyError."""
    log = _mk_log(tmp_path, "worker")
    assert read_events(tmp_path / "space") == []

    payload = {
        "id": "t1",
        "effects": ["email.send"],
        "approval_ref": "deadbeef" * 8,
    }
    with pytest.raises(PolicyError):
        guarded_append(log, "item.create", payload)


# --- guarded_append: acceptance paths -------------------------------------


def test_guarded_append_accepts_valid_grant(tmp_path):
    grant_log = _mk_log(tmp_path, "human")
    grant = grant_log.append("approval.grant", {"item": "t1"})

    log = _mk_log(tmp_path, "worker")
    payload = {
        "id": "t1",
        "title": "send an email",
        "effects": ["email.send"],
        "approval_ref": grant.hash,
    }

    event = guarded_append(log, "item.create", payload)
    assert event.type == "item.create"
    assert event.payload == payload

    events = read_events(tmp_path / "space")
    assert {e.hash for e in events} == {grant.hash, event.hash}


def test_guarded_append_passes_through_non_cosign_events_untouched(tmp_path):
    log = _mk_log(tmp_path, "worker")
    payload = {"id": "t1", "title": "harmless task"}

    event = guarded_append(log, "item.create", payload)
    assert event.payload == payload

    events = read_events(tmp_path / "space")
    assert len(events) == 1
    assert events[0].hash == event.hash


def test_guarded_append_item_claim_never_requires_cosign(tmp_path):
    """item.claim/complete on an already-created side-effect item do NOT
    re-require approval -- the create is the gate, not every downstream op."""
    log = _mk_log(tmp_path, "worker")
    event = guarded_append(log, "item.claim", {"id": "t1", "effects": ["email.send"]})
    assert event.type == "item.claim"


def test_guarded_append_accepts_custom_policy(tmp_path):
    """A custom Policy object can be passed directly (skips load_policy)."""
    policy = Policy(approver="gregor", cosign_effects=frozenset({"custom.effect"}))

    grant_log = _mk_log(tmp_path, "gregor")
    grant = grant_log.append("approval.grant", {"item": "t1"})

    log = _mk_log(tmp_path, "worker")
    payload = {
        "id": "t1",
        "effects": ["custom.effect"],
        "approval_ref": grant.hash,
    }
    event = guarded_append(log, "item.create", payload, policy=policy)
    assert event.type == "item.create"


# --- approval.grant is a plain, first-class event type --------------------


def test_approval_grant_in_event_types():
    assert "approval.grant" in EVENT_TYPES


def test_approval_grant_appendable_via_plain_event_log(tmp_path):
    log = _mk_log(tmp_path, "human")
    event = log.append("approval.grant", {"item": "t1"})
    assert event.type == "approval.grant"

    events = read_events(tmp_path / "space")
    assert events[0].hash == event.hash
