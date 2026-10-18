"""Authorship is not authorization (DIP-0034 October amendment B; Codex audit
2026-10-18, finding 12).

WHY. The hub's admission rule (ledger.hub_admission) proves who WROTE an event:
signed, chained, in its own log, for this space. It never asked whether the
writer was ALLOWED to make that transition. `ledger.policy.guarded_append`
asks exactly that -- but only for a writer that goes through it on its own
machine. A writer that signs an `item.claim` for work addressed to someone
else, an `item.grant` it is not the approver for, or a co-signed `item.create`
without an approval, and sends it to the hub (or publishes it by git), got a
state transition the policy would have refused.

THREE OUTCOMES, kept apart:
  * a validly signed PROPOSAL      -- admission (hub_admission);
  * an authorized STATE TRANSITION -- this module (`authorize`);
  * permission to EXECUTE an effect -- the second confirmation plus any
    co-sign (hub.HubWriter.receipt_check); a seal proves inclusion only.

ONE RULE, TWO PLACES. `authorize(event, state, seen, inputs)` is pure. The hub
asks it at the front door before it stores an append (a refusal is an answer
only), and `fold(..., policy=inputs)` asks it for every event during replay,
so an unauthorized event that arrived by git, a restore or a rebuild is a
recorded no-op in every copy. The rules are the existing policy's:

  * `item.grant`, `approval.grant`: only the approver (policy.py stage 4);
  * `item.claim`: the item's assignee rule (`actor_identity.addressed_to`),
    the principal's never-effects, and a still-valid co-sign; a pool item
    (`role`) or an item that has ever been leased is taken only through a hub
    lease, never through the older claim path (amendment B);
  * `item.create` / `item.update`: the closed effects vocabulary, a co-sign
    (`approval_ref` to the approver's `approval.grant` bound to the exact
    content) when the effects need one or the item was approved before, and
    immutable creation-authority fields;
  * `item.lease`: the holder passes the same execution rule as a claimant
    (`may_execute`: assignee, never-effects, co-sign);
  * `item.complete` of a pool item: only under a lease, and only with a
    DONE_WHEN (it is a submission for review); `item.verify` of a pool item:
    bound to the submission's attempt, task hash and result digest, and by an
    INDEPENDENT EXECUTION IDENTITY -- a registered principal other than the
    holder's, not merely another writer name (`pool_review_problem`,
    amendment G, audit finding 14);
  * `item.dismiss`, `item.release`, `owner.set`, `item.archive`,
    `item.reopen` of another principal's item: the arbitration order, when
    the policy declares one (policy.py stage 5).

EXPLICIT INPUT. `PolicyInputs` is built once, before replay, from the policy
file and the principal registry (`PolicyInputs.load`); the fold never reads
either. NOT YET AS OF EACH EVENT'S TIME: the ledger has no versioned policy
record (`policy.set` has no fold semantics), so a replay judges every event by
the policy as it stands when the replay starts. Also not applied here, because
they read mutable disk state the fold cannot take as input: the per-principal
delegation allowlist, hop limit and daily creation cap (`claim_gate.check_create`)
and the monthly budget (`claim_gate.check_budget`). Both remain open.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

#: Event types this module judges. Everything else is authorized by admission alone.
GOVERNED = frozenset({"item.create", "item.update", "item.claim", "item.grant", "approval.grant",
                      "item.complete", "item.verify", "item.dismiss", "item.release", "owner.set", "item.archive",
                      "item.reopen", "item.lease"})
_ARBITRATED = frozenset({"item.dismiss", "item.release", "owner.set", "item.archive", "item.reopen"})
_CREATION_AUTHORITY = ("requested_by", "hops", "root")


@dataclass(frozen=True)
class PolicyInputs:
    """Everything authorization depends on besides the events (explicit input)."""
    approver: str
    cosign_effects: frozenset
    #: The closed effects vocabulary (None: `cosign_effects`, as policy.py).
    known_effects: frozenset | None = None
    #: Arbitration order across principals (earlier outranks later); () = none declared.
    arbitration: tuple = ()
    #: {writer name: principal name}, resolved from the registry before replay.
    principal_of: Mapping[str, str] = field(default_factory=dict)
    #: {principal: limits} from the policy file (never_effects, cosign_effects).
    limits: Mapping[str, dict] = field(default_factory=dict)

    @classmethod
    def from_policy(cls, policy, principal_of: Mapping[str, str] | None = None) -> "PolicyInputs":
        return cls(approver=policy.approver, cosign_effects=frozenset(policy.cosign_effects),
                   known_effects=(frozenset(policy.known_effects) if policy.known_effects is not None else None),
                   arbitration=tuple(policy.arbitration or ()), principal_of=dict(principal_of or {}),
                   limits={k: dict(v) for k, v in (policy.principals or {}).items()})

    @classmethod
    def load(cls, policy=None) -> "PolicyInputs":
        """Read the policy file and the principal registry ONCE (before replay)."""
        if policy is None:
            from .policy import load_policy
            policy = load_policy()
        return cls.from_policy(policy, _registry_principals())

    @property
    def vocabulary(self) -> frozenset:
        return self.known_effects if self.known_effects is not None else self.cosign_effects


def _registry_principals() -> dict:
    """{writer: principal} from registry/principals.yaml. Unreadable -> {} (stricter:
    only an exact writer match then counts as "addressed to")."""
    try:
        from actor_identity import _writer_names, base_writer, principals
        out: dict = {}
        for name, entry in principals().items():
            for w in _writer_names(entry or {}, name) | {base_writer(name)}:
                out.setdefault(w, name)
        return out
    except Exception:  # noqa: BLE001 -- could-not-read is not permission
        return {}


def _base(actor: str) -> str:
    from actor_identity import base_writer
    return base_writer(actor)


def _principal(actor: str, inputs: PolicyInputs) -> str | None:
    return inputs.principal_of.get(_base(actor))


def addressed(actor: str, assignee, inputs: PolicyInputs) -> bool:
    """`actor_identity.addressed_to`, with the registry as explicit input."""
    if not assignee:
        return True
    if _base(actor) == _base(str(assignee)):
        return True
    mine = _principal(actor, inputs)
    return mine is not None and _principal(str(assignee), inputs) == mine


def _effects_problem(payload: dict, inputs: PolicyInputs) -> str | None:
    effects = payload.get("effects")
    if effects is None:
        return None
    if not isinstance(effects, list) or any(not isinstance(e, str) or not e for e in effects):
        return f"effects must be a list of names (got {effects!r})"
    unknown = sorted(set(effects) - inputs.vocabulary)
    if unknown:
        return f"unknown effect(s) {unknown} (not in the policy's known effects)"
    return None


def _needs_cosign(actor: str, payload: dict, inputs: PolicyInputs) -> bool:
    effects = set(payload.get("effects") or [])
    if effects & set(inputs.cosign_effects):
        return True
    limits = inputs.limits.get(_principal(actor, inputs) or "", {})
    return bool(effects & set(limits.get("cosign_effects") or []))


def _approval_problem(payload: dict, seen: Mapping, inputs: PolicyInputs) -> str | None:
    """`policy.validate_approval`, over the events already authorized."""
    from .policy import approval_payload_hash
    ref = payload.get("approval_ref")
    if not ref:
        return f"effects {sorted(payload.get('effects') or [])} need a co-sign; no approval_ref"
    grant = seen.get(ref)
    if grant is None:
        return f"approval_ref {str(ref)[:12]} names no authorized event"
    if grant.type != "approval.grant" or _base(grant.actor) != _base(inputs.approver):
        return f"approval_ref {str(ref)[:12]} is not an approval.grant by the approver {inputs.approver!r}"
    item = (grant.payload or {}).get("item")
    if not isinstance(item, str) or not item or item != payload.get("id"):
        return f"approval_ref {str(ref)[:12]} grants another item"
    if (grant.payload or {}).get("payload_hash") != approval_payload_hash(payload):
        return "the approval does not bind this exact content"
    return None


def may_execute(actor: str, item, seen: Mapping, inputs: PolicyInputs) -> str | None:
    """Why `actor` may not execute `item`, or None: the assignee rule, the
    principal's never-effects and a still-valid co-sign. One rule for a claim
    and for a lease (the hub asks it before granting, the fold on replay)."""
    p = item.payload or {}
    if not addressed(actor, p.get("assignee"), inputs):
        return f"{item.id} is addressed to {p.get('assignee')!r}"
    limits = inputs.limits.get(_principal(actor, inputs) or "", {})
    never = set(p.get("effects") or []) & set(limits.get("never_effects") or [])
    if never:
        return f"{actor!r} may never perform {', '.join(sorted(never))}"
    if _previously_approved(item) or _needs_cosign(actor, p, inputs):
        return _approval_problem(p, seen, inputs)
    return None


def _previously_approved(item) -> bool:
    return bool((item.payload or {}).get("approval_ref"))


def authorize(event, state, seen: Mapping, inputs: PolicyInputs) -> str | None:
    """Why `event` is not an authorized transition on `state`, or None.

    `state`: the folded state of everything before `event` (a LedgerState).
    `seen`: {hash: event} of the events already authorized (approval lookup).
    Pure: no clock, no file, no network.
    """
    t = event.type
    if t not in GOVERNED:
        return None
    payload = event.payload if isinstance(event.payload, dict) else {}
    actor = event.actor if isinstance(event.actor, str) else ""

    if t in ("item.grant", "approval.grant"):
        if _base(actor) != _base(inputs.approver):
            return f"{t} is the approver's act ({inputs.approver!r}), not {actor!r}'s"
        return None

    if t == "item.create":
        why = _effects_problem(payload, inputs)
        if why:
            return why
        if _needs_cosign(actor, payload, inputs):
            return _approval_problem(payload, seen, inputs)
        return None

    item_id = payload.get("id")
    item = state.items.get(item_id) if isinstance(item_id, str) else None
    if item is None:
        return None                      # the fold records it as an orphan

    if t == "item.update":
        for key in _CREATION_AUTHORITY:
            if key in payload and payload[key] != (item.payload or {}).get(key):
                return f"creation-authority field {key} is immutable"
        from .edits import EditConflict, update_payload
        try:
            merged = update_payload(item, payload)
        except (EditConflict, AttributeError, TypeError):
            return None                  # the fold records the edit conflict itself
        why = _effects_problem(merged, inputs)
        if why:
            return why
        if _previously_approved(item) or _needs_cosign(actor, merged, inputs):
            # Approved once, approved for good: edited content needs a grant
            # bound to the edited content (policy.py, claim_keeps_approval).
            return _approval_problem(merged, seen, inputs)
        return None

    if t == "item.claim":
        p = item.payload or {}
        if p.get("role"):
            return f"{item_id} is a pool item (role {p['role']!r}); it is taken only through a hub lease"
        if item.lease_ref is not None:
            return f"{item_id} has been leased; it is taken only through a hub lease"
        return may_execute(actor, item, seen, inputs)

    if t == "item.lease":
        # A lease is the hub's grant of execution: the same execution policy
        # as a claim, judged for the HOLDER (Codex re-audit, finding 12).
        holder = payload.get("holder")
        if not isinstance(holder, str) or not holder:
            return None                  # the fold records an invalid lease itself
        return may_execute(holder, item, seen, inputs)

    if t == "item.complete":
        if (item.payload or {}).get("role") and item.lease_ref is None:
            return f"{item_id} is a pool item; it is completed only under a hub lease"
    if t in ("item.complete", "item.verify"):
        return pool_review_problem(item, t, actor, payload, inputs)

    if t in _ARBITRATED and inputs.arbitration and item.owner:
        owner_p = _principal(str(item.owner), inputs) or _base(str(item.owner))
        actor_p = _principal(actor, inputs) or _base(actor)
        order = list(inputs.arbitration)
        if owner_p != actor_p and not (actor_p in order and (
                owner_p not in order or order.index(actor_p) < order.index(owner_p))):
            return f"{actor_p} may not override {owner_p}'s item (arbitration: {' > '.join(order)})"
    return None


def pool_review_problem(item, etype: str, actor: str, payload, inputs: PolicyInputs) -> str | None:
    """Why this completion or check of a pool item is not authorized ("code: why"), or None.

    The fold's own rule (`fold.pool_refusal`: DONE_WHEN, digest, binding, not
    the holder's own writer) plus the one the fold cannot see without the
    registry: the checker's EXECUTION IDENTITY. The registry identifies an
    execution identity as a principal (principals.yaml: one entry per agent or
    human, with every writer name it uses). A checker must belong to a
    principal, and to another one than the holder's. What the registry does
    not distinguish, and so this does not check: two separate RUNS of one
    principal, or two principals sharing one process or host.
    """
    from .fold import is_pool_item, pool_refusal
    why = pool_refusal(item, etype, actor, payload)
    if why or etype != "item.verify" or not is_pool_item(item):
        return why
    holder = (item.submission or {}).get("holder") or item.lease_holder or ""
    mine, theirs = _principal(actor, inputs), _principal(str(holder), inputs)
    if mine is None:
        return (f"not_independent: {actor!r} belongs to no registered principal, so its execution identity "
                f"cannot be shown independent of the holder {holder!r}")
    if mine == theirs:
        return (f"not_independent: {actor!r} and the holder {holder!r} are one execution identity "
                f"(principal {mine!r}); another principal must check")
    return None
