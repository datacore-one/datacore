"""Policy gate: the human-in-the-loop enforcement point for side-effecting work.

A `Policy` names one `approver` actor and a set of `cosign_effects` -- effect
tags (e.g. `email.send`, `payment`, `prod.deploy`) that make an `item.create`
event "side-effecting" and therefore require a recorded human grant before it
may be appended at all. `requires_cosign` decides whether a given event needs
that grant; `guarded_append` is the actual gate, and it is the ONLY sanctioned
way for gated code to append such events -- calling `EventLog.append`
directly bypasses it entirely, so callers that need enforcement must go
through this module.

Only `item.create` is gated. Once a side-effecting item has been created
(with a valid grant attached), its downstream lifecycle events --
`item.claim`, `item.complete`, etc. -- are NOT re-checked: the create is the
gate. This keeps the check singular and unambiguous (one grant per item,
referenced once, at creation) rather than requiring a fresh grant for every
event a long-running item ever emits.

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
material, despite living in `.datacore/keys/` alongside the signing-key
registry). A missing file is not an error: it resolves to the default policy
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

TRUST BOUNDARY: while signing is dormant (ENG-2026-0729-030), actor strings
are self-declared -- `approval.grant` authenticity rests on process
boundaries (who can write to the space's `<actor>.jsonl` file), not
cryptography. It becomes cryptographic only when `DATACORE_LEDGER_SIGN=1`
gives the approver a keypair (see `ledger.log.EventLog`'s `sign` parameter
and `ledger.keys`). Until then, this gate prevents ACCIDENTAL ungated side
effects (an item.create slipping into existence with no human ever having
looked at it) -- it does NOT defend against adversarial forgery: any
process able to write to `policy.approver`'s actor file in this space can
forge a self-declared grant. Do not present this gate as tamper-proof
until signing is switched on.

Deterministic: no clock reads, no randomness. The only I/O is reading the
policy YAML file and (via `guarded_append`) scanning the space's event log.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from .events import Event
from .log import EventLog, read_events

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
    if not path.exists():
        return Policy(approver=DEFAULT_APPROVER, cosign_effects=DEFAULT_COSIGN_EFFECTS)

    data = yaml.safe_load(path.read_text())

    if not isinstance(data, dict):
        raise PolicyError(
            f"approvals policy {path}: root must be a mapping (got {type(data).__name__})"
        )

    errors: list[str] = []

    if "version" not in data:
        errors.append(f"{path}: missing required 'version' field (must be 1)")
    elif data["version"] != 1:
        errors.append(f"{path}: 'version' must be 1 (got {data['version']!r})")

    approver = data.get("approver")
    if "approver" not in data:
        errors.append(f"{path}: missing required 'approver' field")
    elif not isinstance(approver, str) or not approver:
        errors.append(f"{path}: 'approver' must be a non-empty string (got {approver!r})")

    raw_effects = data.get("cosign_effects")
    cosign_effects: frozenset[str] = frozenset()
    if "cosign_effects" not in data:
        errors.append(f"{path}: missing required 'cosign_effects' field")
    elif not isinstance(raw_effects, list) or not all(
        isinstance(e, str) and e for e in raw_effects
    ):
        errors.append(
            f"{path}: 'cosign_effects' must be a list of non-empty strings (got {raw_effects!r})"
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
                f"{path}: 'known_effects' must be a list of non-empty strings (got {raw_known!r})"
            )
        else:
            known_effects = frozenset(raw_known)

    principals: dict | None = None
    if "principals" in data:
        raw_p = data.get("principals")
        if not isinstance(raw_p, dict):
            errors.append(f"{path}: 'principals' must be a mapping of name -> limits (got {raw_p!r})")
        else:
            principals = {}
            for name, lim in raw_p.items():
                lim = lim or {}
                if not isinstance(lim, dict):
                    errors.append(f"{path}: principals.{name} must be a mapping (got {lim!r})"); continue
                for k in ("never_effects", "cosign_effects", "may_delegate_to"):
                    v = lim.get(k)
                    if v is not None and not (isinstance(v, list) and all(isinstance(e, str) and e for e in v)):
                        errors.append(f"{path}: principals.{name}.{k} must be a list of non-empty strings (got {v!r})")
                for k in ("max_creates_per_day", "max_hops"):
                    v = lim.get(k)
                    if v is not None and not (isinstance(v, int) and v >= 0):
                        errors.append(f"{path}: principals.{name}.{k} must be a non-negative integer (got {v!r})")
                unknown = sorted(set(lim) - {"never_effects", "cosign_effects", "may_delegate_to", "max_creates_per_day", "max_hops"})
                if unknown:
                    errors.append(f"{path}: principals.{name} has unknown key(s): {', '.join(unknown)}")
                principals[str(name)] = dict(lim)
    arbitration: tuple[str, ...] | None = None
    if "arbitration" in data:
        raw_a = data.get("arbitration")
        if not (isinstance(raw_a, list) and all(isinstance(e, str) and e for e in raw_a)):
            errors.append(f"{path}: 'arbitration' must be a list of non-empty principal names (got {raw_a!r})")
        else:
            arbitration = tuple(raw_a)
    if errors:
        raise PolicyError("\n".join(errors))

    return Policy(approver=approver, cosign_effects=cosign_effects, known_effects=known_effects,
                  principals=principals, arbitration=arbitration)


def requires_cosign(policy: Policy, event_type: str, payload: dict) -> bool:
    """True iff `event_type == "item.create"` and `payload["effects"]`
    intersects `policy.cosign_effects`.

    Only `item.create` is ever gated -- downstream lifecycle events
    (`item.claim`, `item.complete`, ...) against an already-created item
    never require a (re-)grant, regardless of their `effects`. A missing or
    empty `effects` payload never requires cosign.
    """
    if event_type != "item.create":
        return False
    effects = payload.get("effects") or []
    return bool(set(effects) & policy.cosign_effects)


def guarded_append(
    log: EventLog,
    type: str,
    payload: dict,
    policy: Policy | None = None,
    space_dir: Path | None = None,
) -> Event:
    """Append `type`/`payload` to `log`, enforcing the cosign gate first.

    For any `item.create`, `payload.get("effects")` -- if present at all --
    must be a `list`, checked BEFORE `requires_cosign` ever looks at it:
    a malformed `effects` (e.g. a bare string, which Python would happily
    iterate character-by-character) fails closed with `PolicyError` rather
    than silently deciding gating from garbage. (`requires_cosign` itself
    stays permissively typed -- this is the one place that pre-validates
    for it.)

    Immediately after that shape check, every effect named in the list is
    checked against `policy.effective_known_effects` (the closed effects
    vocabulary -- see module docstring). ANY effect not in that set raises
    `PolicyError` naming it, unconditionally -- this runs regardless of
    whether `requires_cosign` would even trigger, so a typo'd effect can
    never silently bypass cosign by simply failing to match
    `cosign_effects` either.

    If `requires_cosign(policy, type, payload)` is False, this is exactly
    `log.append(type, payload)` -- non-gated events pass straight through,
    untouched. (Duplicate/replay `item.create`s for the same `id` are NOT
    rejected here when ungated -- that is `ledger.fold`'s business, which
    already treats a second `item.create` against an existing id as a
    history no-op.)

    If it is True, ALL of the following must hold, checked in order, each
    raising `PolicyError` naming exactly which one failed the instant it
    fails (nothing later is even evaluated):
      1. `payload["approval_ref"]` is present/non-empty.
      2. It is the `hash` of an existing event in the space (found via
         `read_events(space_dir)`, which merges every actor's file).
      3. That event's `type` is `"approval.grant"`.
      4. That event's `actor` equals `policy.approver`.
      5. `payload["id"]` (the new event's own id) is a non-empty string --
         checked explicitly, and BEFORE the comparison below, so a
         cosign-gated create with no `id` at all can never slip through by
         coincidentally matching an equally id-less grant (both sides
         defaulting to `None` would otherwise compare equal -- the
         vacuous-match bypass this method must never allow).
      6. The matched grant's `payload["item"]` is ALSO a non-empty string
         -- likewise checked explicitly before comparing, for the same
         reason: a grant with no item binding must never validate any
         create, no matter what that create's `id` is (or isn't).
      7. `payload["id"] == grant.payload["item"]`.
      8. No event in the space is already an `item.create` with this same
         `id` -- a granted `approval_ref` authorizes creating an item
         exactly once; replaying the same ref against a second attempt at
         the same `id` is rejected as "item already created", not
         silently re-validated.

    All of the above happens BEFORE `log.append` is called, so a rejected
    event never touches the log file.

    `policy` defaults to `load_policy()` (the tracked default path).
    `space_dir` defaults to `log.space_dir` (the space `log` itself writes
    into) -- a space with zero events yet is handled the same as any other:
    the ref simply won't be found.

    See the module docstring's TRUST BOUNDARY note: every actor check here
    is a string comparison against a self-declared field, not yet a
    cryptographic guarantee (that requires `DATACORE_LEDGER_SIGN=1`).
    """
    policy = policy if policy is not None else load_policy()

    if type == "item.grant" and policy is not None:
        # Stage 4: a grant is the approver's act. Any other writer minting a
        # grant would let an agent widen its own authority by delegation.
        who = getattr(log, "actor", None) or ""
        if who != policy.approver:
            raise PolicyError(f"item.grant refused for {who!r}: only the approver ({policy.approver}) may grant")
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

    if type == "item.create":
        effects = payload.get("effects")
        if effects is not None and not isinstance(effects, list):
            raise PolicyError(
                f"item.create payload['effects'] must be a list (got {effects!r})"
            )
        # Stage 4/5 (product description): who is asking, how deep the chain
        # is, and how much this writer has created today — decided before the
        # append, refused loudly, never silently dropped in the fold.
        try:
            from claim_gate import check_create
        except ImportError:  # root lib not importable here (older caller); the effects gate below still applies
            check_create = None
        # Active only once the installation declares principals in the policy
        # file; a bare Policy() (older callers, unit tests) keeps the old gate.
        if check_create is not None and getattr(policy, "principals", None) is not None:
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

    if not requires_cosign(policy, type, payload):
        return log.append(type, payload)

    space_dir = space_dir if space_dir is not None else log.space_dir

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

    if any(e.type == "item.create" and e.payload.get("id") == item_id for e in events):
        raise PolicyError(f"item already created: {item_id!r}")

    return log.append(type, payload)
