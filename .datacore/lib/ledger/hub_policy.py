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
    the policy declares one (policy.py stage 5);
  * OWNERSHIP CHANGES (Codex round-3 verdict, `reassign`, `owner_override`):
    an `item.update` that changes `assignee` is made only by the approver,
    the item's creator, its current owner or its current assignee, or by an
    actor taking an item it may already execute (`may_execute`); an
    `owner.set` names an owner that may execute the item, unless the
    approver makes it; an `item.create` that carries `owner` names the
    creator itself, addressed by the assignment, unless the approver makes it.

EXPLICIT INPUT. `PolicyInputs` is built once, before replay, from the policy
file and the principal registry (`PolicyInputs.load`); the fold never reads
either.

POLICY VERSIONS (owner decision 1, round 8; closes the round-7 audit's R10).
In a hub space the policy is not the file: it is the ledger's own record.
Every policy change is a ledger record signed by the owner, and each event is
judged by the policy in force when the hub admitted it, so a later policy
change never changes an earlier outcome.
  * The owner's `hub.enable` records the policy in force at activation
    (`PolicyInputs.to_record`, principal map included). Everything before
    the first recorded change -- pre-activation history too -- is judged by it.
  * A change is an owner-signed `policy.set` in the owner's log (an owner
    record: only the owner's chain may hold one). It takes effect only when
    the active grantor admits it: the grantor's `ledger.admit` (kind
    "policy") records the FRONTIER, each log's highest seq the hub held at
    that moment. An event at or below the frontier of its log was admitted
    under the earlier policy; an event above it -- including one that
    reaches the hub by git later -- under the new one.
  * `PolicyLedger` carries the activation policy and the owner into the fold;
    `PolicyLedger.versions(events, ...)` derives the versions from the
    events alone, the same on every replica. The policy file a hub or reader
    was started with is ignored in a hub space (the hub reports a difference
    in `status`).
Also not applied here, because
they read mutable disk state the fold cannot take as input: the per-principal
delegation allowlist, hop limit and daily creation cap (`claim_gate.check_create`)
and the monthly budget (`claim_gate.check_budget`). Both remain open.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
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

    def to_record(self) -> dict:
        """The policy as a ledger record body (JSON, canonical order): what the
        owner signs in `hub.enable` and `policy.set`."""
        out = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, (frozenset, set)):
                v = sorted(v)
            elif isinstance(v, tuple):
                v = list(v)
            elif isinstance(v, Mapping):
                v = {str(k): (dict(x) if isinstance(x, Mapping) else x) for k, x in sorted(v.items())}
            out[f.name] = v
        return {"version": 1, "inputs": out}

    @classmethod
    def from_record(cls, record) -> "PolicyInputs":
        """The inverse of `to_record`. Raises ValueError for anything malformed
        (a malformed policy record is never read as "no policy")."""
        if not isinstance(record, dict) or record.get("version") != 1 or not isinstance(record.get("inputs"), dict):
            raise ValueError("not a version-1 policy record")
        raw = record["inputs"]
        known = {f.name: f for f in fields(cls)}
        if set(raw) - set(known):
            raise ValueError(f"unknown policy fields {sorted(set(raw) - set(known))}")
        if not isinstance(raw.get("approver"), str) or not raw["approver"]:
            raise ValueError("a policy names its approver")
        kw: dict = {"approver": raw["approver"]}
        for name in ("cosign_effects", "known_effects"):
            if name not in raw:
                continue
            v = raw[name]
            if v is None and name == "known_effects":
                kw[name] = None
            elif isinstance(v, list) and all(isinstance(x, str) for x in v):
                kw[name] = frozenset(v)
            else:
                raise ValueError(f"policy field {name} must be a list of names")
        if "cosign_effects" not in kw:
            raise ValueError("a policy names its co-sign effects")
        if "arbitration" in raw:
            v = raw["arbitration"]
            if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
                raise ValueError("policy field arbitration must be a list of names")
            kw["arbitration"] = tuple(v)
        if "principal_of" in raw:
            v = raw["principal_of"]
            if not isinstance(v, dict) or any(not isinstance(k, str) or not isinstance(x, str) for k, x in v.items()):
                raise ValueError("policy field principal_of must map writer names to principals")
            kw["principal_of"] = dict(v)
        if "limits" in raw:
            v = raw["limits"]
            if not isinstance(v, dict) or any(not isinstance(k, str) or not isinstance(x, dict) for k, x in v.items()):
                raise ValueError("policy field limits must map principals to limits")
            kw["limits"] = {k: dict(x) for k, x in v.items()}
        return cls(**kw)


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


def _attempt_obligations(item, merged: dict, actor: str, inputs: PolicyInputs) -> str | None:
    """A live lease attempt keeps the obligations it was granted with (Codex
    re-audit, new defect 5): who it is addressed to, its role and its DONE_WHEN
    change only by the approver's act (the owner's re-approval), never by the
    holder or another writer while the attempt runs."""
    from .fold import done_when_of
    ref = item.lease_ref or {}
    if item.lease_holder is None or ref.get("ended") or _base(actor) == _base(inputs.approver):
        return None
    old = item.payload or {}
    changed = [k for k in ("assignee", "role") if old.get(k) != merged.get(k)]
    if done_when_of(old) != done_when_of(merged):
        changed.append("DONE_WHEN")
    if changed:
        return (f"{item.id} has a live lease attempt; changing its {', '.join(changed)} needs the "
                f"approver ({inputs.approver!r})")
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


def _creator(item) -> str | None:
    """Who created `item`: the fold's own record of the applied `item.create`
    (`_note`: "<hlc> <actor> <type>: <outcome>"), the same in every copy."""
    for line in item.history or ():
        parts = line.split(" ", 3)
        if len(parts) == 4 and parts[2] == "item.create:" and parts[3] == "applied":
            return parts[1]
    return None


def _same(a, b, inputs: PolicyInputs) -> bool:
    """Same writer or same principal."""
    return bool(a) and bool(b) and addressed(str(a), str(b), inputs)


def _reassign_problem(actor: str, item, new_assignee, seen: Mapping, inputs: PolicyInputs) -> str | None:
    """Why `actor` may not change `item`'s assignee to `new_assignee`, or None.

    Assignment decides who may execute, so changing it is an ownership change
    (Codex round-3 verdict, `reassign`): the approver, the creator, the current
    owner and the current assignee may hand the item on; anyone else may only
    take an item it could already claim."""
    p = item.payload or {}
    if (_same(actor, inputs.approver, inputs) or _same(actor, _creator(item), inputs)
            or _same(actor, item.owner, inputs) or _same(actor, p.get("assignee"), inputs)):
        return None
    if new_assignee and _same(actor, new_assignee, inputs) and may_execute(actor, item, seen, inputs) is None:
        return None
    return (f"{actor!r} may not reassign {item.id} (assigned to {p.get('assignee')!r}): only the approver, "
            f"its creator, owner or assignee may")


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
        owner = payload.get("owner")
        if owner and not _same(actor, inputs.approver, inputs):
            # The fold takes `owner` straight from the creation, so it is an
            # ownership change too: the creator may own only what it could
            # claim; naming anyone else is the approver's override.
            if not _same(actor, owner, inputs) or not addressed(str(owner), payload.get("assignee"), inputs):
                return (f"item.create with owner {owner!r} refused: a creation's owner is its creator, "
                        f"addressed by the assignment (assignee {payload.get('assignee')!r})")
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
        why = _attempt_obligations(item, merged, actor, inputs)
        if why:
            return why
        why = _effects_problem(merged, inputs)
        if why:
            return why
        if getattr(item, "pool_work", False) and any(
                merged.get(k) != (item.payload or {}).get(k) for k in ("assignee", "role")) and not (
                _same(actor, inputs.approver, inputs) or _same(actor, _creator(item), inputs)):
            # POOL WORK STAYS POOL WORK (Codex round-3 verdict,
            # `pool_release_reclassify`): only the approver or the task's
            # creator addresses a pool task to an agent or changes its role.
            return (f"{item_id} is pool work; only the approver ({inputs.approver!r}) or its creator "
                    "may address it to an agent or change its role")
        if merged.get("assignee") != (item.payload or {}).get("assignee"):
            why = _reassign_problem(actor, item, merged.get("assignee"), seen, inputs)
            if why:
                return why
        if _previously_approved(item) or _needs_cosign(actor, merged, inputs):
            # Approved once, approved for good: edited content needs a grant
            # bound to the edited content (policy.py, claim_keeps_approval).
            return _approval_problem(merged, seen, inputs)
        return None

    if t == "item.claim":
        p = item.payload or {}
        if p.get("role") or getattr(item, "pool_work", False):
            return f"{item_id} is a pool item (role {p.get('role')!r}); it is taken only through a hub lease"
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
        if ((item.payload or {}).get("role") or getattr(item, "pool_work", False)) and item.lease_ref is None:
            return f"{item_id} is a pool item; it is completed only under a hub lease"
    if t in ("item.complete", "item.verify"):
        return pool_review_problem(item, t, actor, payload, inputs)

    if t == "owner.set":
        # An owner is who executes: the new owner passes the claim's rule
        # (Codex round-3 verdict, `owner_override`), unless the approver --
        # the admin override -- sets it. Clearing the owner is arbitration only.
        new_owner = payload.get("owner")
        if getattr(item, "pool_work", False) and not _same(actor, inputs.approver, inputs):
            # The owner of pool work is the hub's lease holder; only the
            # approver's admin override sets it otherwise (Codex round-3 verdict).
            return f"owner.set on {item_id} refused: it is pool work, owned only through a hub lease"
        if new_owner and not _same(actor, inputs.approver, inputs):
            why = may_execute(str(new_owner), item, seen, inputs)
            if why:
                return f"owner.set to {new_owner!r} refused: {why}"

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

# ── policy versions (owner decision 1, round 8) ─────────────────────────────

POLICY_TYPE = "policy.set"
POLICY_ADMIT_KIND = "policy"


@dataclass(frozen=True)
class PolicyLedger:
    """The fold's policy input for a hub space: the activation policy (from
    the owner's `hub.enable`) and the owner, from which the versions are
    derived out of the events themselves (`versions`)."""
    initial: PolicyInputs
    owner: str

    def versions(self, events, lease_grantors=None) -> "PolicyVersions":
        """The policy versions these events record, oldest first.

        A version counts only when (a) the owner's own chain holds a
        `policy.set` with a valid policy record, and (b) a grantor of the hub
        it names admitted it with a `ledger.admit` (kind "policy") naming its
        log, seq and hash and carrying the frontier, and no grantor rejected
        it. Decided from the events alone: no file, no clock."""
        from .finality import ADMIT_TYPE, REJECT_TYPE
        grantors: dict = {}
        for hub, actors in (lease_grantors or {}).items():
            grantors[hub] = dict(actors) if isinstance(actors, Mapping) else {a: 0 for a in actors}
        owner = _base(self.owner) if self.owner else ""
        records = {}
        for e in events:
            chain = getattr(e, "log", None) or e.actor
            if (e.type == POLICY_TYPE and owner and chain == e.actor and _base(e.actor) == owner
                    and isinstance(e.payload, dict)):
                records[e.hash] = e
        if not records:
            return PolicyVersions(((None, self.initial),))
        rejected, admits = set(), []
        for e in events:
            if e.type not in (ADMIT_TYPE, REJECT_TYPE):
                continue
            hub = getattr(e, "hub", "") or ""
            if (getattr(e, "log", None) or e.actor) != e.actor or e.actor not in grantors.get(hub, {}):
                continue
            p = e.payload if isinstance(e.payload, dict) else {}
            ref = p.get("event") if isinstance(p.get("event"), dict) else {}
            if ref.get("hash") not in records:
                continue
            if e.type == REJECT_TYPE:
                rejected.add(ref["hash"])
            elif p.get("kind") == POLICY_ADMIT_KIND:
                admits.append((grantors[hub][e.actor], e.seq if type(e.seq) is int else -1, e, ref))
        out = [(None, self.initial)]
        done = set()
        for _, _, a, ref in sorted(admits, key=lambda x: (x[0], x[1])):
            rec = records[ref["hash"]]
            if ref["hash"] in done or ref["hash"] in rejected or ref.get("seq") != rec.seq \
                    or ref.get("log") != rec.actor:
                continue
            frontier = a.payload.get("frontier")
            if not isinstance(frontier, dict) or any(not isinstance(k, str) or type(v) is not int
                                                     for k, v in frontier.items()):
                continue
            try:
                inputs = PolicyInputs.from_record(rec.payload.get("policy"))
            except (ValueError, TypeError):
                continue
            done.add(ref["hash"])
            out.append((dict(frontier), inputs))
        return PolicyVersions(tuple(out))


@dataclass(frozen=True)
class PolicyVersions:
    """((frontier or None, PolicyInputs), ...), oldest first."""
    versions: tuple

    def for_event(self, event) -> PolicyInputs:
        """The policy in force when the hub admitted `event`: the latest version
        whose frontier lies below the event's position in its log."""
        chain = getattr(event, "log", None) or event.actor
        seq = event.seq if type(event.seq) is int else None
        for frontier, inputs in reversed(self.versions):
            if frontier is None:
                return inputs
            if seq is not None and seq > frontier.get(chain, -1):
                return inputs
        return self.versions[0][1]

    @property
    def latest(self) -> PolicyInputs:
        return self.versions[-1][1]
