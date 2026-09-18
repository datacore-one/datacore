"""Policy gate: the human-in-the-loop enforcement point for side-effecting work.

A `Policy` names one `approver` actor and a set of `cosign_effects` -- effect
tags (e.g. `email.send`, `payment`, `prod.deploy`) that make an `item.create`
event "side-effecting" and therefore require a recorded human grant before it
may be appended at all. `requires_cosign` decides whether a given event needs
that grant; `guarded_append` is the actual gate, and it is the ONLY sanctioned
way for gated code to append such events -- calling `EventLog.append`
directly bypasses it entirely, so callers that need enforcement must go
through this module.

Creation, updates, and claims validate the complete current payload. A grant
remains usable for unchanged content; editing approved content requires a new
content-bound grant. Claim-time validation also catches unguarded imports.

The grant mechanism: a payload requiring cosign must carry `approval_ref`,
the `hash` of an existing event in the same space (per `ledger.log.read_events`,
which merges every actor's file) such that ALL of the following hold:
  - an event with that hash exists at all
  - its `type` is `"approval.grant"`
  - its `actor` equals `policy.approver`
  - the new event's `payload["id"]` is a non-empty string
  - the grant's `payload["item"]` is ALSO a non-empty string (both sides
    are checked for presence BEFORE they are compared -- two missing
    fields must never compare equal to each other; see `guarded_append`)
  - `payload["item"]` equals the new event's `payload["id"]`
  - no event in the space is already an `item.create` for that same `id`
    (a grant authorizes creation exactly once; replaying the same
    `approval_ref` against a second create attempt is rejected, not
    silently re-validated)
Any failure raises `PolicyError` naming which condition failed. Validation
happens entirely BEFORE `log.append` is called -- a rejected event never
touches the log file, by construction (this module never calls `append`
until every check has passed).

Policy is loaded from YAML at `<DATACORE_ROOT>/.datacore/config/approvals_policy.yaml`
by default (a tracked, public, secret-free file -- it is policy, not key
material). A missing file is not an error: it resolves to the default policy
(`approver="human"`, `cosign_effects={email.send, payment, prod.deploy}`,
`known_effects` falling back to that same set -- see below).
A *present but malformed* file raises `PolicyError` listing every problem
found, one per line (never just the first) -- same style as
`jobs.manifest.load_manifest`.

CLOSED EFFECTS VOCABULARY (final-review wave): the policy file may also
carry an optional `known_effects` list -- the complete, closed vocabulary
of valid effect tags an `item.create` may declare. When absent, it defaults
to `cosign_effects` (so a policy that never mentions `known_effects` at
all behaves exactly as before this amendment: only the three default
cosign effects are "known"). `guarded_append` checks every effect named in
an `item.create`'s `effects` list against this vocabulary -- fail closed:
an effect that is not in `known_effects` raises `PolicyError` naming it,
regardless of whether it would have required cosign at all. This closes a
silent-bypass gap: a typo'd effect (e.g. `emial.send`) would not intersect
`cosign_effects` either, so `requires_cosign` would (wrongly) say no grant
is needed and the create would sail through ungated. Registering the full
set of legitimate effects in `known_effects` turns that silent miss into a
loud, immediate rejection. A legitimate non-cosign effect must be
explicitly added to a custom `known_effects` list to be usable at all.

TRUST BOUNDARY: this is a cooperative control within one owner-controlled
installation. Unsigned actor strings are self-declared. With signing enabled,
validate_approval verifies a grant against the configured public-key registry;
that only establishes an independent identity boundary if approver private
keys, registry changes and the consuming policy are protected from the caller.
Sharing an OS identity or credentials does not provide that protection. Signing
alone does not prevent raw execution, policy bypass or cross-host duplicate
side effects. Deployments needing those guarantees require independent access
controls and an execution/commit authority that enforces them.

The local policy lock serializes cooperating appenders on this host. It does
not serialize an independent host or an unguarded writer.

"""

from __future__ import annotations

import os
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from .events import Event
from .log import EventLog, read_events
from file_utils import file_lock

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))

DEFAULT_POLICY_PATH = DATACORE_ROOT / ".datacore" / "config" / "approvals_policy.yaml"

DEFAULT_APPROVER = "human"
DEFAULT_COSIGN_EFFECTS = frozenset({"email.send", "payment", "prod.deploy"})


class PolicyError(ValueError):
    """Raised by `load_policy` (malformed policy YAML, every problem listed,
    one per line) and by `guarded_append` (a cosign-gated event whose
    `approval_ref` is missing, dangling, or does not name a valid grant --
    the message says exactly which check failed).
    """


@dataclass(frozen=True)
class Policy:
    approver: str
    cosign_effects: frozenset[str]
    # None means "not configured" -- effective_known_effects() falls back
    # to cosign_effects. Kept optional (rather than eagerly resolved at
    # construction) so a bare `Policy(approver=..., cosign_effects=...)`
    # -- as built directly by every pre-existing caller/test -- keeps
    # behaving exactly as it did before this field existed.
    known_effects: frozenset[str] | None = None
    #: Per-principal limits (product description, stage 4): `never_effects`
    #: (refused outright, no grant can allow them), `cosign_effects` (added to
    #: the global set for this principal), `may_delegate_to` (who this
    #: principal may address an item to), `max_creates_per_day`, `max_hops`.
    #: None means the file declares no principals section; claim_gate applies
    #: its documented defaults then.
    principals: dict | None = None
    #: Arbitration order across principals (stage 5): earlier outranks later.
    arbitration: tuple[str, ...] | None = None

    @property
    def effective_known_effects(self) -> frozenset[str]:
        """The closed effects vocabulary to validate `item.create` effects
        against: `known_effects` when set, else `cosign_effects`."""
        return self.known_effects if self.known_effects is not None else self.cosign_effects


def load_policy(path: Path | None = None) -> Policy:
    """Load the approvals policy from `path` (default: the tracked
    `<DATACORE_ROOT>/.datacore/config/approvals_policy.yaml`).

    A missing file is NOT an error: it returns the default policy
    (`approver="human"`, `cosign_effects={email.send, payment, prod.deploy}`,
    `known_effects` falling back to that same set). A present file must
    have the shape `{version: 1, approver: <str>, cosign_effects: [<str>,
    ...], known_effects: [<str>, ...]?}`; any deviation raises `PolicyError`
    with every problem found listed, one per line. `known_effects` is
    optional -- when absent, `Policy.known_effects` is `None` and
    `effective_known_effects` falls back to `cosign_effects`. Unknown
    top-level keys are ignored (forward compatibility).
    """
    path = Path(path) if path is not None else DEFAULT_POLICY_PATH
    if not path.exists() and not path.is_symlink():
        return Policy(approver=DEFAULT_APPROVER, cosign_effects=DEFAULT_COSIGN_EFFECTS)

    from yaml_safety import UniqueStringKeyLoader
    try:
        data = yaml.load(path.read_text(encoding='utf-8'), Loader=UniqueStringKeyLoader)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError):
        raise PolicyError('approvals policy is unreadable or ambiguous') from None

    if not isinstance(data, dict):
        raise PolicyError(
            f"approvals policy {path}: root must be a mapping (got {type(data).__name__})"
        )

    errors: list[str] = []

    if "version" not in data:
        errors.append(f"{path}: missing required 'version' field (must be 1)")
    elif type(data["version"]) is not int or data["version"] != 1:
        errors.append(f"{path}: 'version' must be integer 1")

    approver = data.get("approver")
    if "approver" not in data:
        errors.append(f"{path}: missing required 'approver' field")
    elif not isinstance(approver, str) or not approver:
        errors.append(f"{path}: 'approver' must be a non-empty string")

    raw_effects = data.get("cosign_effects")
    cosign_effects: frozenset[str] = frozenset()
    if "cosign_effects" not in data:
        errors.append(f"{path}: missing required 'cosign_effects' field")
    elif not isinstance(raw_effects, list) or not all(
        isinstance(e, str) and e for e in raw_effects
    ):
        errors.append(
            f"{path}: 'cosign_effects' must be a list of non-empty strings"
        )
    else:
        cosign_effects = frozenset(raw_effects)

    known_effects: frozenset[str] | None = None
    if "known_effects" in data:
        raw_known = data.get("known_effects")
        if not isinstance(raw_known, list) or not all(
            isinstance(e, str) and e for e in raw_known
        ):
            errors.append(
                f"{path}: 'known_effects' must be a list of non-empty strings"
            )
        else:
            known_effects = frozenset(raw_known)

    principals: dict | None = None
    if "principals" in data:
        raw_p = data.get("principals")
        if not isinstance(raw_p, dict):
            errors.append(f"{path}: 'principals' must be a mapping of name -> limits")
        else:
            principals = {}
            for name, lim in raw_p.items():
                if not isinstance(lim, dict):
                    errors.append(f"{path}: each principals entry must be a mapping"); continue
                for k in ("never_effects", "cosign_effects", "may_delegate_to"):
                    v = lim.get(k)
                    if v is not None and not (isinstance(v, list) and all(isinstance(e, str) and e for e in v)):
                        errors.append(f"{path}: principals entry '{k}' must be a list of non-empty strings")
                for k in ("max_creates_per_day", "max_hops"):
                    v = lim.get(k)
                    if v is not None and not (type(v) is int and v >= 0):
                        errors.append(f"{path}: principals entry '{k}' must be a non-negative integer")
                unknown = sorted(set(lim) - {"never_effects", "cosign_effects", "may_delegate_to", "max_creates_per_day", "max_hops"})
                if unknown:
                    errors.append(f"{path}: principals entry has unknown key(s)")
                principals[str(name)] = dict(lim)
    arbitration: tuple[str, ...] | None = None
    if "arbitration" in data:
        raw_a = data.get("arbitration")
        if not (isinstance(raw_a, list) and all(isinstance(e, str) and e for e in raw_a)):
            errors.append(f"{path}: 'arbitration' must be a list of non-empty principal names")
        else:
            arbitration = tuple(raw_a)
    if errors:
        raise PolicyError("\n".join(errors))

    return Policy(approver=approver, cosign_effects=cosign_effects, known_effects=known_effects,
                  principals=principals, arbitration=arbitration)


def requires_cosign(policy: Policy, event_type: str, payload: dict) -> bool:
    """True iff `event_type == "item.create"` and `payload["effects"]`
    intersects `policy.cosign_effects`.

    This predicate identifies effectful proposals. guarded_append also applies
    it to post-update and claimed content and retains an existing approval
    requirement. A missing or empty effects list alone does not require cosign.
    """
    if event_type != "item.create":
        return False
    effects = payload.get("effects") or []
    return bool(set(effects) & policy.cosign_effects)


def approval_payload_hash(payload: dict) -> str:
    """Bind approval to complete proposed content, not just its chosen ID."""
    bound = {key: value for key, value in payload.items()
             if key not in {"approval_ref", "assignee_absent"}}
    bound.setdefault("effects", [])
    encoded = json.dumps(bound, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def guarded_append(
    log: EventLog,
    type: str,
    payload: dict,
    policy: Policy | None = None,
    space_dir: Path | None = None,
) -> Event:
    """Bind the policy read and append to one space and one local lock.

    The lock serializes cooperating processes on this host. Independent
    offline hosts are not globally serialized by this lock; deployment must
    use a single execution authority for effects that require exclusivity.
    """
    actual_space = Path(log.space_dir).resolve()
    if space_dir is not None and Path(space_dir).resolve() != actual_space:
        raise PolicyError("approval space differs from the destination space")
    if not isinstance(payload, dict):
        raise PolicyError("event payload must be an object")
    with file_lock(actual_space / ".datacore" / "state" / "policy"):
        return _guarded_append_locked(log, type, payload, policy, actual_space)


def _guarded_append_locked(
    log: EventLog,
    type: str,
    payload: dict,
    policy: Policy | None = None,
    space_dir: Path | None = None,
) -> Event:
    """Validate the writer and exact resulting content before appending.

    Creation authority and effect vocabulary are checked on create. Updates
    preserve creation authority and validate the same merged content as replay.
    Claims bind current content and revalidate approval, assignment, policy and
    availability. An already approved item keeps its approval requirement even
    if an unguarded update removes its effects. Explicit reconciliation is
    required before conflicted content can be edited or claimed.

    A rejected operation never calls log.append. The caller holds this space's
    local policy lock; this does not establish distributed execution exclusion.
    """
    policy = policy if policy is not None else load_policy()

    append_payload = payload
    previously_approved = False
    if type in {"item.claim", "item.update"}:
        from .fold import fold
        events = read_events(space_dir)
        item = fold(events).items.get(payload.get("id"))
        if item is None:
            raise PolicyError("item does not exist")
        if item.edit_conflicts:
            resolution = payload.get('_merge')
            if (type != 'item.update' or not isinstance(resolution, dict)
                    or not isinstance(resolution.get('resolves'), list)
                    or not all(isinstance(key, str) for key in resolution['resolves'])
                    or not set(item.edit_conflicts).issubset(resolution.get('resolves', []))):
                raise PolicyError('unresolved replicated edits require explicit reconciliation')
        previously_approved = any(e.type == "item.create" and e.payload.get("id") == item.id
                                  and e.payload.get("approval_ref") for e in events)
        if type == "item.update":
            if item.status != "created":
                raise PolicyError("item content cannot change after execution has started")
            for key in ("requested_by", "hops", "root"):
                if key in payload and payload[key] != item.payload.get(key):
                    raise PolicyError(f"creation authority field {key} is immutable")
            from claim_gate import check_override
            allowed, reason = check_override(log.actor, item.id, space_dir, policy)
            if not allowed:
                raise PolicyError(reason)
            from .edits import EditConflict, update_payload
            try:
                payload = update_payload(item, payload)
            except EditConflict as exc:
                raise PolicyError(str(exc)) from exc
        else:
            payload = dict(item.payload)

    if type == "item.claim":
        from .fold import fold
        from claim_gate import check_claim
        item = fold(read_events(space_dir)).items.get(payload.get("id"))
        if item is None or item.status != "created":
            raise PolicyError("item is missing or no longer available to claim")
        current_hash = approval_payload_hash(item.payload)
        if append_payload.get("payload_hash") not in (None, current_hash):
            raise PolicyError("item changed after dispatch selection; select it again")
        append_payload = {**append_payload, "payload_hash": current_hash}
        who = log.actor
        assignee = (item.payload or {}).get("assignee")
        if assignee:
            from actor_identity import addressed_to
            if not addressed_to(who, assignee):
                raise PolicyError("item is assigned to another principal")
        ok, reason = check_claim(who, item.payload, policy=policy, space_dir=space_dir)
        if not ok:
            raise PolicyError(reason)

    if type in {"item.grant", "approval.grant"} and policy is not None:
        # Stage 4: a grant is the approver's act. Any other writer minting a
        # grant would let an agent widen its own authority by delegation.
        who = getattr(log, "actor", None) or ""
        if who != policy.approver:
            raise PolicyError(f"{type} refused for {who!r}: only the approver ({policy.approver}) may grant")
    if type in ("item.dismiss", "item.release", "owner.set") and policy is not None and getattr(policy, "arbitration", None):
        # Stage 5: closing, releasing or reassigning ANOTHER principal's item is
        # arbitration, and the order in the policy file decides who may.
        who = getattr(log, "actor", None) or ""
        sd = space_dir or getattr(log, "space_dir", None)
        if sd is not None:
            try:
                from claim_gate import check_override
            except ImportError:
                check_override = None
            if check_override is not None:
                _ok, _why = check_override(who, payload.get("id"), Path(sd), policy=policy)
                if not _ok:
                    raise PolicyError(f"{type} refused for {who!r}: {_why}")

    if type in {"item.create", "item.update", "item.claim"}:
        effects = payload.get("effects")
        if effects is not None and (not isinstance(effects, list) or any(not isinstance(e, str) or not e for e in effects)):
            raise PolicyError(
                f"item.create payload['effects'] must be a list (got {effects!r})"
            )
        # Stage 4/5 (product description): who is asking, how deep the chain
        # is, and how much this writer has created today — decided before the
        # append, refused loudly, never silently dropped in the fold.
        from claim_gate import check_create
        # Active only once the installation declares principals in the policy
        # file; a bare Policy() (older callers, unit tests) keeps the old gate.
        if type == "item.create" and getattr(policy, "principals", None) is not None:
            _ok, _why = check_create(getattr(log, "actor", None) or "", payload, policy=policy,
                                     space_dir=space_dir or getattr(log, "space_dir", None))
            if not _ok:
                raise PolicyError(f"item.create refused for {getattr(log, 'actor', '?')}: {_why}")

        if effects:
            known = policy.effective_known_effects
            unknown = [e for e in effects if e not in known]
            if unknown:
                raise PolicyError(
                    f"item.create payload['effects'] names unknown effect(s) "
                    f"{unknown!r} -- not in policy's known_effects "
                    f"{sorted(known)!r} (fail closed: register the effect or "
                    "fix the typo)"
                )

    needs_cosign = (type in {"item.create", "item.update", "item.claim"}
                   and (previously_approved or requires_cosign(policy, "item.create", payload)))
    if type in {"item.create", "item.update", "item.claim"} and policy.principals:
        from actor_identity import principal_of
        principal = principal_of(log.actor)[0]
        limits = policy.principals.get(principal, {})
        needs_cosign |= bool(set(payload.get("effects") or []) & set(limits.get("cosign_effects") or []))
    if not needs_cosign:
        return log.append(type, append_payload)

    validate_approval(log, payload, policy, creating=type == "item.create")
    return log.append(type, append_payload)


def validate_approval(log: EventLog, payload: dict, policy: Policy, *, creating=False) -> None:
    """Validate one content-bound grant without appending or changing state."""
    space_dir = log.space_dir

    approval_ref = payload.get("approval_ref")
    if not approval_ref:
        raise PolicyError(
            f"item.create with effects {sorted(payload.get('effects') or [])!r} requires "
            "cosign but payload is missing 'approval_ref'"
        )

    events = read_events(space_dir)

    grant = next((e for e in events if e.hash == approval_ref), None)
    if grant is None:
        raise PolicyError(
            f"approval_ref {approval_ref!r} does not match any event hash in space {space_dir}"
        )
    if grant.type != "approval.grant":
        raise PolicyError(
            f"approval_ref {approval_ref!r} refers to a {grant.type!r} event, not 'approval.grant'"
        )
    if grant.actor != policy.approver:
        raise PolicyError(
            f"approval_ref {approval_ref!r} was granted by actor {grant.actor!r}, "
            f"but policy requires approver {policy.approver!r}"
        )

    if getattr(log, "sign", False):
        from .events import body_dict, canonical_bytes, compute_hash
        from .keys import verify
        body = body_dict(grant.seq, grant.hlc, grant.actor, grant.type, grant.payload, grant.prev)
        if grant.hash != compute_hash(body) or not grant.sig or not verify(
                grant.actor, canonical_bytes(body), grant.sig, registry_path=log.registry_path):
            raise PolicyError("approval grant signature could not be verified")

    item_id = payload.get("id")
    if not isinstance(item_id, str) or not item_id:
        raise PolicyError("gated item.create requires a non-empty id")

    grant_item = grant.payload.get("item")
    if not isinstance(grant_item, str) or not grant_item:
        raise PolicyError(
            f"approval_ref {approval_ref!r} refers to an approval.grant missing item binding"
        )

    if grant_item != item_id:
        raise PolicyError(
            f"approval_ref {approval_ref!r} grants item {grant_item!r}, "
            f"which does not match this event's id {item_id!r}"
        )

    if creating and any(e.type == "item.create" and e.payload.get("id") == item_id for e in events):
        raise PolicyError(f"item already created: {item_id!r}")

    if grant.payload.get("payload_hash") != approval_payload_hash(payload):
        raise PolicyError("approval does not bind this payload; obtain a grant for the exact proposed content")
