"""Deterministic fold: reduce an event list into item state, ownership, and spend.

`fold` is a pure function -- no clock reads, no randomness, no filesystem
or network I/O. Given the same `events` list it always returns an equal
`LedgerState`, and it never mutates its input (neither the list nor the
`Event`/`payload` objects in it).

Events arrive already merged and hlc-sorted by the caller (see
`ledger.log.read_events`) -- fold() trusts that ordering completely and
does NOT re-sort. "Earliest HLC wins" for competing operations (e.g. two
actors racing to claim the same item) therefore falls out for free: fold()
just applies events strictly in list order, and the first one to satisfy a
transition's precondition wins; every later one that no longer satisfies it
is recorded as a no-op in that item's history.

`item.dismiss` is terminal: once an item's status is "dismissed", its
status, owner, grant, closing stamp and content never change again --
every later event addressed to it, including the `owner.set` admin
override, is a no-op for those -- except `item.reopen`, the one explicit
revival (ledger upgrade Phase 4; Lean Item.reopen_is_the_only_way_back).

`item.archive` hides an item without closing it: status "archived", content
kept, nothing can claim or reassign it, and `item.reopen` brings it back
(Item.archive_is_revivable). The projector renders neither archived items nor
anything outside its live and recently-closed statuses.

The one exception is `edit_conflicts`, deliberately. A conditional edit that
races a dismissal is refused and RECORDED as a conflict (the projector then
refuses the space until it is reconciled), and a conditional `item.dismiss`
carrying `resolves` is the route out (ledger_resolve_conflict.py). Both are
logged as what they are, never as a no-op. Proved in
specs/datacore-lean (Item.dismissed_frozen, Item.dismissed_conflict_rule).

`item.release` means "un-claim", never "un-complete": it is legal ONLY
when status is "claimed". A release attempt against a completed, verified,
or (already) created item is a no-op naming the blocking status -- it does
not silently regress a completed/verified item back to "created".

Events that reference an item id with no prior `item.create` (e.g. a claim
that arrives before its create, or one that never arrives) are not
invented into phantom item entries. They are recorded in
`LedgerState.orphans` instead, so diagnostics can see them without the
fold ever fabricating item state.

Poison-event defense (final-review wave, ENG conservation-floor amendment):
fold() is a substrate primitive -- it must never brick a space just
because one event in the log has a malformed payload. Two shapes are
handled explicitly, both empirically confirmed (via ledger_cli) to raise
`KeyError` before this pass:

  - An `item.*`-family event whose payload is missing `"id"`, or whose
    `"id"` is present but not a non-empty string, is routed to
    `LedgerState.orphans` as `"{hlc} {type} -"` instead of being looked up
    (which would KeyError) or fabricated into item state. This is the
    same orphan mechanism used for a valid id that simply names no known
    item -- `_orphan` now honestly renders "-" for an absent/invalid id
    instead of carrying dead `.get("id", "?")` fallback code that could
    never actually be reached (any missing "id" used to KeyError first).
  - `spend.record` with a `"cents"` that is missing, not an `int`, is a
    `bool` (Python's `bool` is an `int` subclass -- `isinstance(True, int)`
    is `True`, so it must be excluded explicitly), or is negative is
    skipped entirely (no balance mutation) and recorded as an orphan
    `"{hlc} spend.record invalid"`. Rejecting negative cents here is the
    substrate-side half of the ledger's conservation floor: spend only
    ever accumulates, it never goes backwards via a poisoned event.

No payload shape -- for any event type fold() handles -- may raise. A
malformed event is always turned into an orphans entry and folding
continues; it is never silently dropped and never crashes the whole fold.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .events import Event


@dataclass
class ItemState:
    id: str
    title: str
    owner: str | None
    status: str  # created | claimed | completed | verified | dismissed
    #: HLC of the event that closed this item (completed or dismissed).
    #: Needed because closing is a MOMENT, and the projection now renders
    #: recently-closed work as DONE before archiving it -- "when" is the only
    #: thing that distinguishes the two.
    closed_at: str | None = None
    #: Why it closed. `item.dismiss` is OVERLOADED: org ingestion emits it when
    #: a human marks a task DONE (the fold refuses item.complete on an
    #: unclaimed item), and the dead-letter emits it for giving up. Same event,
    #: opposite meanings -- without the reason a report calls finished work
    #: "cancelled", which is worse than not reporting at all.
    closed_reason: str | None = None
    #: Declared closure kind from the dismiss payload: "done" | "dropped" |
    #: "housekeeping". Emitters state intent; readers stop guessing.
    closed_kind: str | None = None
    history: list[str] = field(default_factory=list)
    #: The `item.create` payload, copied verbatim. The fold's own state is
    #: deliberately minimal, but a PROJECTOR (DIP-0043) has to rebuild a full
    #: org heading -- scheduled, deadline, tags, parent, body -- and those live
    #: only in the payload. Without this a projection cannot be derived from
    #: folded state at all, only by re-reading raw events, which would give the
    #: projector a second, divergent view of history. Copied (not aliased) so
    #: fold stays non-mutating over its input.
    payload: dict = field(default_factory=dict)
    claimed_payload_hash: str | None = None
    #: Who granted execution, and when. `item.grant` is declared in events.py
    #: as the DIP-0034 amendment -- "a claim is a PROPOSAL; execution in an
    #: arbitrated pool requires the arbiter's grant" -- and policy.py enforces
    #: that only the approver may mint one. The fold had no handler for it, so a
    #: grant changed nothing, appeared in no item's history, and did not move
    #: the state root: a control that is declared, guarded, and absent. Recorded
    #: here so a consumer can require it; nothing is retroactively required.
    granted_by: str | None = None
    granted_at: str | None = None
    edit_conflicts: dict[str, str] = field(default_factory=dict)
    #: HUB LEASES (DIP-0034 October 2026 amendment B). The current holder, the
    #: hub-clock expiry, and which lease event granted it ({hub, log, seq,
    #: hash}). `lease_ref` stays set once an item has been leased: from then on
    #: a completion by anyone but the current holder is a recorded conflict.
    lease_holder: str | None = None
    lease_expires_ms: int | None = None
    lease_ref: dict | None = None
    #: event hash -> why: overlapping leases (the later one loses) and
    #: completions by an actor who did not hold the lease. Never a silent no-op.
    lease_conflicts: dict[str, str] = field(default_factory=dict)
    #: event hash -> why: results that arrived after the grantor ended their
    #: attempt (audit finding 9). Not completions; the owner decides.
    lease_unresolved: dict[str, str] = field(default_factory=dict)
    #: ROLE POOLS (amendment G; audit finding 14). A pool item's completion is
    #: a SUBMISSION FOR REVIEW: {attempt, task_hash, result_digest,
    #: role_sha256, holder}. Status `submitted` until a check by another
    #: execution identity, bound to exactly this, makes it `verified`.
    submission: dict | None = None
    #: POOL WORK STAYS POOL WORK (Codex round-3 verdict, `pool_release_reclassify`).
    #: Set when the item is created as a pool task or leased as one, never
    #: cleared: whoever it is addressed to later, its completion is a
    #: submission for an independent check.
    pool_work: bool = False


@dataclass
class LedgerState:
    items: dict[str, ItemState] = field(default_factory=dict)
    spend: dict[str, int] = field(default_factory=dict)
    orphans: list[str] = field(default_factory=list)
    #: ROLE POOLS (amendment G). "hub|grantor|role" -> {cents_per_day, space}, from `pool.budget`.
    budgets: dict[str, dict] = field(default_factory=dict)
    #: "role|YYYY-MM-DD" (UTC day of the spend's HLC) -> cents, from hub-stamped
    #: `spend.record` events naming a pool item (a role and no assignee).
    pool_spend: dict[str, int] = field(default_factory=dict)
    #: event hash -> what folding it did ("applied ...", "no-op (...)",
    #: "conflict (...)", "recorded ...", "orphan"): the last note the event
    #: left on its item. A receipt asks this whether ONE operation's intended
    #: transition took effect (Codex re-audit, finding 3): a covered event that
    #: folded to a no-op is not a confirmation. Derived from the same fold, so
    #: deliberately outside `state_root` (it adds nothing a root must pin).
    outcomes: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    def state_root(self) -> str:
        """One hash over the WHOLE folded state — items, spend and orphans.

        Two machines answer "do we agree?" by comparing this instead of
        replaying two logs. All three fields participate deliberately: hashing
        only `items` would attest agreement while spend totals differed or one
        machine had silently accumulated orphans, which is worse than no root —
        a confident answer to a question it did not ask.

        Encoded with the same `canonical_bytes` the event hash uses, so the
        result is stable across Python versions and dict ordering rather than
        merely stable within one process.

        A mismatch is NOT on its own an alarm: two machines mid-convergence hold
        different event sets and must differ. It is only meaningful once
        per-actor `seq` agrees, which is the seq-gap detector's job — checking
        the root first would make it noisy by construction, and a noisy alarm is
        an ignored one.
        """
        import hashlib
        from .events import canonical_bytes

        h = hashlib.sha256()
        for iid in sorted(self.items):
            it = self.items[iid]
            document = asdict(it)
            # Keep historical state roots stable when no new conflict exists.
            if not document['edit_conflicts']:
                del document['edit_conflicts']
            # Same rule for the grant fields: an item nobody granted hashes
            # exactly as it did before they existed, so adding the handler does
            # not invalidate every checkpoint ever written.
            #
            # BOTH must be unset, not just `granted_by`. Keying the drop on one
            # field took the OTHER out of the hash with it, so a state carrying
            # `granted_at` and no grantor sealed identically to one carrying
            # neither -- the seal's whole invariant is that no field can change
            # without changing the root. `_handle_grant` writes the pair
            # together, so nothing that exists today is affected; a fold that
            # ever sets one alone is exactly the case a seal must catch.
            if document.get('granted_by') is None and document.get('granted_at') is None:
                document.pop('granted_by', None)
                document.pop('granted_at', None)
            # Same rule for leases: a space that never leased hashes as before.
            if (document.get('lease_ref') is None and document.get('lease_holder') is None
                    and document.get('lease_expires_ms') is None and not document.get('lease_conflicts')):
                for key in ('lease_holder', 'lease_expires_ms', 'lease_ref', 'lease_conflicts'):
                    document.pop(key, None)
            if not document.get('lease_unresolved'):
                document.pop('lease_unresolved', None)
            if document.get('submission') is None:
                document.pop('submission', None)
            if not document.get('pool_work'):
                document.pop('pool_work', None)
            h.update(canonical_bytes({"key": iid, "item": document}))
        h.update(canonical_bytes({"spend": dict(sorted(self.spend.items()))}))
        h.update(canonical_bytes({"orphans": sorted(self.orphans)}))
        # Pool state enters the root only once it exists, so every space that
        # never used pools keeps the roots its seals already name.
        if self.budgets or self.pool_spend:
            h.update(canonical_bytes({"pools": {"budgets": self.budgets, "spend": self.pool_spend}}))
        return h.hexdigest()


def fold(events: list[Event], *, lease_grantors=None, policy=None) -> LedgerState:
    """Reduce `events` (already merged + hlc-sorted) into a `LedgerState`.

    Pure and order-preserving: does not sort, does not mutate `events` or
    any `Event`/payload within it, and produces byte-for-byte identical
    output for identical input on every call.

    `lease_grantors` ({hub id: actors}, audit finding 2): who may grant,
    renew and record lease conflicts for each hub, passed in by a caller that
    authenticated it (ledger.hub reads it from the space's hub config before
    the fold starts, never during it). A lease event by anyone else is a
    no-op. None keeps the structural rule alone ("has a hub field"); only a
    caller that has already filtered through ledger.hub_admission may rely on
    that.

    `policy` (a `ledger.hub_policy.PolicyInputs`, audit finding 12): when
    given, every governed event must also be an AUTHORIZED transition
    (`hub_policy.authorize`: assignee, approver, co-sign, pool items only
    through a lease, arbitration). An unauthorized one is a validly signed
    proposal that changes nothing: a recorded no-op. Explicit input, read by
    the caller before the fold starts; None keeps the fold as it was.
    """
    state = LedgerState()
    seen: dict = {}
    authorize = None
    if policy is not None:
        from .hub_policy import authorize

    # Events cancelled by an authorised in-ledger `ledger.void` do not exist for
    # the fold (LED-4). Resolving who may void reads the principal registry
    # (cached on its stat), the one input besides `events`; a space with no
    # void pays nothing.
    from .voids import VOID_TYPE, closed_reversals, from_events
    closed: dict = {}
    if any(e.type == VOID_TYPE for e in events):
        # THE CORRECTION WINDOW (round 5): in a hub space (the caller passed the
        # grantors) a reversal outside it has no effect, whatever else says so.
        if lease_grantors is not None:
            closed = closed_reversals(events, {a for grantors in lease_grantors.values() for a in grantors})
        voids = from_events([e for e in events if e.hash not in closed])
        if len(voids):
            events = [e for e in events if not voids.applies(getattr(e, "log", e.actor), e)]
        state.orphans.extend(f"{e.hlc} ledger.void no-op (outside the correction window)"
                             for e in events if e.hash in closed)

    # REJECTED PROPOSALS ARE NO-OPS (Codex re-audit, new defect 1). An event a
    # grantor recorded as rejected (`ledger.reject`, audit finding 11) was
    # refused at the front door; re-judging it on replay could make it valid
    # (a claim backdated before a reassignment was). It is taken out before
    # lease planning and policy, in every copy, from authenticated grantor
    # events only -- so only when the caller passes the grantors.
    rejected = _rejected_by_grantors(events, lease_grantors) if lease_grantors is not None else frozenset()

    # Leases are resolved before anything is applied (audit finding 8).
    if any(e.type in _PLANNED_TYPES for e in events):
        state._lease_plan = _plan_leases([e for e in events if e.hash not in rejected], lease_grantors)

    for event in events:
        item_id = _valid_item_id(event.payload if isinstance(event.payload, dict) else {})
        before = state.items.get(item_id) if item_id else None
        notes, orphans = (len(before.history) if before is not None else 0), len(state.orphans)
        _step(state, event, seen, rejected, lease_grantors, authorize, policy)
        after = state.items.get(item_id) if item_id else None
        new = (after.history[notes:] if after is before else after.history) if after is not None else []
        if new:
            state.outcomes[event.hash] = new[-1].split(": ", 1)[1] if ": " in new[-1] else new[-1]
        elif len(state.orphans) > orphans:
            state.outcomes[event.hash] = "orphan"

    return state


def _rejected_by_grantors(events, lease_grantors) -> frozenset:
    """Hashes named by `ledger.reject` records a grantor wrote in its own log."""
    out = set()
    for e in events:
        if e.type != "ledger.reject" or (getattr(e, "log", None) or e.actor) != e.actor:
            continue
        if e.actor not in lease_grantors.get(getattr(e, "hub", "") or "", ()):
            continue
        ref = (e.payload or {}).get("event") if isinstance(e.payload, dict) else None
        if isinstance(ref, dict) and isinstance(ref.get("hash"), str):
            out.add(ref["hash"])
    return frozenset(out)


def _step(state: LedgerState, event: Event, seen: dict, rejected, lease_grantors, authorize, policy) -> None:
    """Fold one event: rejected proposal, lease authority, policy, then its handler."""
    handler = _HANDLERS.get(event.type)
    item = state.items.get(_valid_item_id(event.payload if isinstance(event.payload, dict) else {}) or "")
    if event.hash in rejected:
        if item is not None:
            _note(item, event, "no-op (rejected proposal: the grantor refused it at the front door)")
        else:
            state.orphans.append(f"{event.hlc} {event.type} rejected proposal")
        return
    if (lease_grantors is not None and event.type in _LEASE_TYPES
            and event.actor not in lease_grantors.get(getattr(event, "hub", "") or "", ())):
        if item is not None:
            _note(item, event, f"no-op ({event.actor!r} is not a grantor of hub {getattr(event, 'hub', '')!r})")
        return
    if authorize is not None:
        why = authorize(event, state, seen, policy)
        if why:
            if item is not None:
                _note(item, event, f"no-op (not authorized: {why})")
            else:       # never silently dropped: a proposal without an item is listed
                state.orphans.append(f"{event.hlc} {event.type} not authorized")
            return
        seen[event.hash] = event
    if handler is not None:
        handler(state, event)
    # Event types outside this fold's scope (metric.attest,
    # artifact.attest, policy.set, ...) are silently ignored here --
    # they don't affect item/ownership/spend state.


def _note(item: ItemState, event: Event, outcome: str) -> None:
    item.history.append(f"{event.hlc} {event.actor} {event.type}: {outcome}")


def _valid_item_id(payload: dict) -> str | None:
    """Extract `payload["id"]` iff it is a non-empty string.

    Returns `None` for a missing key, a non-string value, or an empty
    string -- callers route to `_orphan` in all three cases instead of
    ever indexing `state.items` with something that isn't a real id (or
    raising `KeyError` for a plain-missing key)."""
    item_id = payload.get("id")
    return item_id if isinstance(item_id, str) and item_id else None


def _orphan(state: LedgerState, event: Event) -> None:
    """Record `event` in `state.orphans`, formatted `"{hlc} {type} {id}"`.

    `id` renders as the event's own `payload["id"]` when that is a valid
    (non-empty string) item id that simply names no known item, or as
    `"-"` when the id itself is missing/invalid -- this is the formerly
    dead `.get("id", "?")` fallback, now honestly reachable (a missing/
    invalid id used to raise `KeyError` in the caller before ever reaching
    here; see `_valid_item_id`)."""
    item_id = _valid_item_id(event.payload)
    state.orphans.append(f"{event.hlc} {event.type} {item_id if item_id is not None else '-'}")


def _get_item_or_orphan(state: LedgerState, event: Event) -> ItemState | None:
    item_id = _valid_item_id(event.payload)
    if item_id is None:
        _orphan(state, event)
        return None
    item = state.items.get(item_id)
    if item is None:
        _orphan(state, event)
    elif item.edit_conflicts and event.type in ('item.claim', 'item.grant', 'item.complete', 'item.verify'):
        _note(item, event, 'no-op (unresolved edit conflicts)')
        return None
    return item


def _dismissed(state: LedgerState, event: Event, item: ItemState) -> bool:
    """If `item` is already terminal, note the no-op and report True.

    Called by every handler except `item.create` (a duplicate create
    against a dismissed item is still just a no-op, but the "item already
    exists" framing there covers it) so that no later event -- including
    the `owner.set` admin override -- can revive a dismissed item or alter
    its status, owner, grant or content. Conditional `item.update` and
    `item.dismiss` handle their `_merge` precondition first, so they can
    still record or reconcile an edit conflict on a dismissed item; see the
    module docstring.
    """
    if item.status == "dismissed":
        _note(item, event, "no-op (item dismissed)")
        return True
    return False


def _handle_create(state: LedgerState, event: Event) -> None:
    payload = event.payload
    item_id = _valid_item_id(payload)
    if item_id is None:
        _orphan(state, event)
        return
    existing = state.items.get(item_id)
    if existing is not None:
        if existing.status == "dismissed":
            _note(existing, event, "no-op (item dismissed)")
        else:
            _note(existing, event, "no-op (item already exists)")
        return

    item = ItemState(
        id=item_id,
        title=payload.get("title", ""),
        owner=payload.get("owner"),
        status="created",
        payload=dict(payload),
        pool_work=bool(pool_role(payload)),
    )
    _note(item, event, "applied")
    state.items[item_id] = item


def _handle_claim(state: LedgerState, event: Event) -> None:
    item = _get_item_or_orphan(state, event)
    if item is None or _dismissed(state, event, item):
        return
    if item.status != "created":
        _note(item, event, f"no-op (already claimed, status={item.status})")
        return
    item.owner = event.actor
    item.claimed_payload_hash = event.payload.get("payload_hash")
    item.status = "claimed"
    _note(item, event, "applied")


def _handle_grant(state: LedgerState, event: Event) -> None:
    """Record the arbiter's grant of execution on a claimed item.

    policy.py already refuses a grant from anyone but the approver, so reaching
    the fold means it was minted by the right principal. What was missing is the
    other half: the fold kept no trace, so `item.grant` was a control that could
    be demanded, refused, audited at the gate -- and then vanished.

    A grant does NOT claim, complete or reassign. It says one thing: this owner
    may execute this item. Granting something nobody has claimed, or something
    already closed, is a no-op that names why, exactly like the other handlers.
    """
    item = _get_item_or_orphan(state, event)
    if item is None or _dismissed(state, event, item):
        return
    if item.status not in ("claimed",):
        _note(item, event, f"no-op (grant illegal from status={item.status})")
        return
    if item.granted_by is not None:
        _note(item, event, f"no-op (already granted by {item.granted_by!r})")
        return
    item.granted_by = event.actor
    item.granted_at = event.hlc
    _note(item, event, f"applied (execution granted to {item.owner!r})")


def _handle_release(state: LedgerState, event: Event) -> None:
    """`item.release` means "un-claim", never "un-complete".

    Legal ONLY when the event's actor is the current owner AND the item's
    status is "claimed" -- a release on a completed/verified/created item
    is a no-op naming the blocking status, not a silent regression back to
    "created". (Ownership is checked first: a non-owner's release is
    always "not owner", regardless of status.)
    """
    item = _get_item_or_orphan(state, event)
    if item is None or _dismissed(state, event, item):
        return
    if item.owner != event.actor:
        _note(item, event, f"no-op (not owner, owner={item.owner!r})")
        return
    if item.status != "claimed":
        _note(item, event, f"no-op (release illegal from status={item.status})")
        return
    if item.lease_ref is not None and (item.lease_ref or {}).get("attempt") and (
            (event.payload or {}).get("attempt") != item.lease_ref["attempt"]):
        _note(item, event, "no-op (release names another lease attempt)")
        return
    if (item.lease_ref or {}).get("task_hash") and (
            (event.payload or {}).get("task_hash") != item.lease_ref["task_hash"]):
        # Like a result, a release names the task content its attempt pinned
        # (Codex re-audit, finding 9): one for other content releases nothing.
        _note(item, event, "no-op (release names other task content than its lease pinned)")
        return
    item.owner = None
    item.status = "created"
    # Releasing a lease returns the item to the pool; `lease_ref` stays, so a
    # later completion by the released holder is still a recorded conflict.
    item.lease_holder = None
    item.lease_expires_ms = None
    # A grant authorises THIS claim's owner. Kept across a release it made the
    # next claimant read as granted, and refused that claimant a real grant as
    # "already granted". (Lean: Item.grant_belongs_to_claim.)
    item.granted_by = None
    item.granted_at = None
    _note(item, event, "applied")


def _handle_complete(state: LedgerState, event: Event) -> None:
    item = _get_item_or_orphan(state, event)
    if item is None or _dismissed(state, event, item):
        return
    if (item.lease_ref is not None or _plan_of(state, item.id)) and not _leased_result(
            state, item, event, "completion"):
        # A leased item: only the current holder completes it, for the current
        # attempt. Anyone else's completion is the double run the lease exists
        # to expose (rule D), never a silent no-op.
        return
    if item.status != "claimed":
        _note(item, event, f"no-op (illegal transition from status={item.status})")
        return
    if item.owner != event.actor:
        _note(item, event, f"no-op (not owner, owner={item.owner!r})")
        return
    why = pool_refusal(item, event.type, event.actor, event.payload)
    if why:
        _note(item, event, f"no-op ({why})")
        return
    if is_pool_item(item):
        # Submitted for review, not done (amendment G): it records what was
        # submitted, under which attempt and which role instructions.
        ref = item.lease_ref or {}
        item.status = "submitted"
        item.submission = {"attempt": ref.get("attempt"), "task_hash": ref.get("task_hash"),
                           "result_digest": result_digest(event.payload),
                           "role_sha256": (ref.get("role") or {}).get("sha256"), "holder": event.actor}
        _note(item, event, "applied (submitted for review)")
        return
    item.status = "completed"
    item.closed_at = event.hlc
    _note(item, event, "applied")


def _handle_update(state: LedgerState, event: Event) -> None:
    """Merge changed fields into an existing item.

    Only the keys present in the payload are touched, so an update that carries
    just `scheduled` cannot silently blank a `deadline` it never mentioned —
    partial updates are the normal case and a whole-payload replace would make
    every caller responsible for resending fields it does not own.

    A dismissed item stays dismissed: dismiss is terminal by DIP-0034, and an
    update must not resurrect what a human closed.
    """
    item = _get_item_or_orphan(state, event)
    if item is None:
        return
    from .edits import update_payload, EditConflict
    fields = {k: v for k, v in (event.payload or {}).items() if k not in {"id", "_merge"}}
    reconciled: list[str] = []
    if '_merge' in event.payload:
        try:
            updated = update_payload(item, event.payload)
            for key in event.payload['_merge'].get('resolves', []):
                if item.edit_conflicts.pop(key, None) is not None:
                    reconciled.append(key)
        except EditConflict as exc:
            item.edit_conflicts[event.hash] = str(exc)
            _note(item, event, 'conflict (preserved; explicit reconciliation required)')
            return
    elif _dismissed(state, event, item):
        return
    else:
        updated = update_payload(item, event.payload)
    if not fields:
        # A field-less conditional update can still reconcile conflicts; that
        # is a state change and is logged as one, never as a no-op.
        # (Lean: Item.noop_means_unchanged.)
        if reconciled:
            _note(item, event, f"applied (reconciled {len(reconciled)} conflict(s))")
        else:
            _note(item, event, "no-op (no fields)")
        return
    item.payload = updated
    if "title" in fields:
        item.title = updated["title"]
    _note(item, event, f"applied ({', '.join(sorted(fields))})")


def _handle_verify(state: LedgerState, event: Event) -> None:
    item = _get_item_or_orphan(state, event)
    if item is None or _dismissed(state, event, item):
        return
    if item.status != ("submitted" if is_pool_item(item) else "completed"):
        _note(item, event, f"no-op (illegal transition from status={item.status})")
        return
    why = pool_refusal(item, event.type, event.actor, event.payload)
    if why:
        _note(item, event, f"no-op ({why})")
        return
    # Separation of duties: the owner completed it (`_handle_complete`
    # requires owner == actor), so the owner verifying is the judge being the
    # defendant. Nothing in the fold or in policy.guarded_append stopped it.
    # (Lean: Item.verify_needs_second_actor.)
    if event.actor == item.owner:
        _note(item, event, "no-op (completer cannot verify own work)")
        return
    item.status = "verified"
    if item.submission is not None:
        item.closed_at = event.hlc          # a pool item is done when the review accepts it
    _note(item, event, "applied")


def _handle_dismiss(state: LedgerState, event: Event) -> None:
    item = _get_item_or_orphan(state, event)
    if item is None:
        return
    reconciled: list[str] = []
    if '_merge' in event.payload:
        from .edits import apply_condition, EditConflict
        try:
            if not event.payload['_merge'].get('terminal'):
                raise EditConflict('dismissal requires a complete content precondition')
            apply_condition(item, event.payload)
            for key in event.payload['_merge'].get('resolves', []):
                if item.edit_conflicts.pop(key, None) is not None:
                    reconciled.append(key)
        except (EditConflict, AttributeError) as exc:
            item.edit_conflicts[event.hash] = str(exc)
            _note(item, event, 'conflict (preserved; explicit reconciliation required)')
            return
    if item.status == "dismissed":
        # Reconciling a dismissed item is the route out of a conflict retained
        # after dismissal (ledger_resolve_conflict.py). It changes state, so it
        # must not be logged as a no-op -- it used to be, while it deleted
        # conflicts. (Lean: Item.dismissed_frozen / dismissed_conflicts_logged.)
        if reconciled:
            _note(item, event, f"applied (reconciled {len(reconciled)} conflict(s); item stays dismissed)")
        else:
            _note(item, event, "no-op (item dismissed)")
        return
    item.status = "dismissed"
    item.closed_at = event.hlc
    payload = event.payload or {}
    item.closed_reason = payload.get("reason")
    item.closed_kind = payload.get("kind")
    _note(item, event, "applied")



#: Reason fragments that mean an item closed as ADMINISTRATIVE HOUSEKEEPING --
#: no work was done and none was abandoned. Deduplication and id-churn cleanup
#: dominate a real space: 0-personal held 240 of these against 4 genuine
#: completions, so folding them into either bucket misreports by ~60x.
_HOUSEKEEPING = ("duplicate", "orphaned", "id churn", "supersed", "re-created",
                 "recreated", "no org task")
#: Reason fragments that mean the work was given up on.
_DROPPED = ("gave up", "cancelled", "canceled", "abandoned", "obsolete",
            "no longer", "bad check path")


def closure_kind(item) -> str:
    """Why an item closed: "done" | "dropped" | "housekeeping".

    PREFERS A DECLARED `kind` ON THE DISMISS PAYLOAD. `item.dismiss` is
    overloaded three ways -- org ingestion emits it when a human marks a task
    DONE, the dead-letter emits it for giving up, maintenance emits it for
    duplicates -- so the event alone cannot say which happened. An emitter
    knows; a reader can only guess.

    The string matching below is the FALLBACK for events written before `kind`
    existed. It is genuinely unreliable and was measured so: probing it with
    realistic novel wordings, "task withdrawn by requester", "rolled back after
    review" and "not doing this" all classified as done, and every
    misclassification fell the same way -- toward done, the direction that
    inflates the weekly report. An empty or absent reason does too.

    So the fallback exists to read history, not to carry the future. New
    emitters must set `kind`.
    """
    if item.status in ("completed", "verified"):
        return "done"
    if item.status == "archived":
        return "housekeeping"   # hidden, not finished and not given up
    if item.status != "dismissed":
        return "done"

    declared = (getattr(item, "closed_kind", None) or "").strip().lower()
    if declared in ("done", "dropped", "housekeeping"):
        return declared

    reason = (item.closed_reason or "").lower()
    if any(h in reason for h in _HOUSEKEEPING):
        return "housekeeping"
    if any(d in reason for d in _DROPPED):
        return "dropped"
    return "done"


def org_task_state(item):
    """Effective GTD state; ownership completion still awaits review."""
    payload = item.payload or {}
    if payload.get('section'):
        return None
    if item.status in ('verified', 'dismissed'):
        return 'DONE' if was_finished(item) else 'CANCELLED'
    if item.status in ('completed', 'submitted'):
        return 'REVIEW'
    return payload.get('state') or 'TODO'


def was_finished(item) -> bool:
    """True only for work that was actually completed."""
    return closure_kind(item) == "done"


def _handle_owner_set(state: LedgerState, event: Event) -> None:
    item = _get_item_or_orphan(state, event)
    if item is None or _dismissed(state, event, item):
        return
    if item.status == "archived":
        _note(item, event, "no-op (item archived)")
        return
    new_owner = event.payload.get("owner")
    # Reassignment voids the grant for the same reason release does; a
    # no-change override leaves it alone.
    if new_owner != item.owner:
        item.granted_by = None
        item.granted_at = None
    item.owner = new_owner
    _note(item, event, "applied (owner override)")


def _valid_cents(payload: dict) -> int | None:
    """Extract `payload["cents"]` iff it is a non-negative, non-bool `int`.

    `bool` is a subclass of `int` in Python (`isinstance(True, int)` is
    `True`), so it is excluded explicitly -- a stray `True`/`False` must
    never silently fold into spend as `1`/`0`. Negative values are
    rejected too: this is the substrate-side half of the ledger's
    conservation floor -- spend only ever accumulates, never regresses via
    a poisoned event. Missing or non-int values are likewise invalid.
    Returns `None` for every invalid case; callers route to orphans
    instead of mutating `state.spend` or raising `KeyError`."""
    cents = payload.get("cents")
    if isinstance(cents, bool) or not isinstance(cents, int):
        return None
    if cents < 0:
        return None
    return cents


def _handle_spend(state: LedgerState, event: Event) -> None:
    cents = _valid_cents(event.payload)
    if cents is None:
        state.orphans.append(f"{event.hlc} spend.record invalid")
        return
    state.spend[event.actor] = state.spend.get(event.actor, 0) + cents
    # A pool's daily spending: hub-stamped spend on a pool item (a role and no
    # assignee). Unstamped (pre-hub) spend never counts, so old folds are unchanged.
    item_id = event.payload.get("item")
    item = state.items.get(item_id) if isinstance(item_id, str) and getattr(event, "hub", "") else None
    role = pool_role(item.payload) if item is not None else None
    day = _utc_day(event.hlc) if role else None
    if role and isinstance(role, str) and day:
        key = f"{role}|{day}"
        state.pool_spend[key] = state.pool_spend.get(key, 0) + cents


def _handle_reopen(state: LedgerState, event: Event) -> None:
    """Bring a dismissed or archived item back to open (Lean Item.reopen_revives).

    Owner, grant and closing stamp are cleared -- a reopened task is unclaimed
    work again; its content, retained edit conflicts and history are kept.
    Completed and verified work is never reopened (Item.reopen_of_open_work_is_noop).
    """
    item = _get_item_or_orphan(state, event)
    if item is None:
        return
    if item.status not in ("dismissed", "archived"):
        _note(item, event, f"no-op (reopen illegal from status={item.status})")
        return
    item.status = "created"
    item.owner = None
    item.granted_by = None
    item.granted_at = None
    item.closed_at = None
    item.closed_reason = None
    item.closed_kind = None
    _note(item, event, "applied")


def _handle_archive(state: LedgerState, event: Event) -> None:
    """Hide an item without closing it (Lean Item.archive_is_revivable)."""
    item = _get_item_or_orphan(state, event)
    if item is None or _dismissed(state, event, item):
        return
    if item.status == "archived":
        _note(item, event, "no-op (item archived)")
        return
    item.status = "archived"
    item.closed_at = event.hlc
    item.closed_reason = (event.payload or {}).get("reason")
    item.granted_by = None
    item.granted_at = None
    _note(item, event, "applied")


# ── HUB LEASES (DIP-0034 October 2026 amendment, section D) ─────────────────
#
# A lease is written only by a grantor (ledger.hub), which grants it only when
# no live lease exists. Two overlapping leases therefore mean a partition or a
# mis-promoted second hub, and the fold must give every copy the same answer
# without a network.
#
# RESOLVE LEASES FIRST (audit finding 8). Before any event is applied, the
# fold decides, per item, which lease attempts win (`_plan_leases`). Lease
# arbitration is a scoped exception to "earliest HLC wins": lease-type events
# (grants, renewals, expiries) are ordered by (promotion epoch, sequence number
# in that epoch's grantor log); within one epoch the grantor log is a single
# total order, and admission refuses a grantor event whose HLC contradicts its
# sequence (hub_admission, bad_clock). Ties that admission makes impossible
# fall back to (log, hash), so the order is total. Only then are events
# applied in list order, and a completion consults the plan, so a completion
# applied first can no longer hide an earlier lease.
#
# INTERVALS are half-open, [granted, end). Two leases overlap when each begins
# before the other ends. `end` starts at `expires_ms`; a renewal of the same
# LIVE winning attempt extends it; an explicit `item.lease.expire` ends the
# attempt at `expired_ms`. A losing lease never becomes the winner by renewal.
#
# ATTEMPTS (audit finding 9). Every lease names an immutable attempt id and
# the task-content hash. Renewals, expiries, releases and results name the
# attempt; one naming another attempt has no effect on the current one. A
# result for an attempt the grantor has already expired is not a completion:
# if it lies beyond the holder-log frontier the expiry recorded (so the hub
# admitted it after the expiry), it is recorded in `lease_unresolved` for the
# owner. A lease written before attempts existed is its own attempt (keyed by
# its hash) and keeps the old holder rule.
#
# Times inside lease payloads are the GRANTOR's clock. The fold has no "now":
# whether a lease is live at a moment is `lease_live(item, now_ms)`, asked by
# the hub, outside the fold.

def task_content_hash(payload: dict) -> str:
    """The task-content hash a lease pins (sha256 of the item's canonical payload)."""
    import hashlib
    from .events import canonical_bytes
    return hashlib.sha256(canonical_bytes(payload if isinstance(payload, dict) else {})).hexdigest()


def _lease_conflict(item: ItemState, event: Event, why: str) -> None:
    item.lease_conflicts.setdefault(event.hash, why)
    _note(item, event, f"conflict ({why})")


@dataclass
class _Attempt:
    key: str
    holder: str
    start: int
    end: int
    epoch: int
    order: tuple
    task_hash: str | None
    named: bool                 # the lease named an attempt id (protocol with attempts)
    won: bool = True
    why: str = ""
    ended: bool = False
    frontier: dict | None = None


@dataclass
class _ItemPlan:
    attempts: dict = field(default_factory=dict)      # attempt key -> _Attempt
    by_event: dict = field(default_factory=dict)      # lease event hash -> attempt key
    outcome: dict = field(default_factory=dict)       # renewal/expiry hash -> None (applies) | why not


def _epoch_of(event: Event, lease_grantors) -> int:
    hub = getattr(event, "hub", "") or ""
    grantors = (lease_grantors or {}).get(hub)
    if isinstance(grantors, dict):
        return int(grantors.get(event.actor, 0))
    epoch = (event.payload or {}).get("epoch") if isinstance(event.payload, dict) else None
    return epoch if type(epoch) is int and epoch >= 0 else 0


def _attempt_key(payload: dict, event: Event) -> str:
    attempt = payload.get("attempt")
    return attempt if isinstance(attempt, str) and attempt else event.hash


def _plan_leases(events, lease_grantors) -> dict:
    """{item id: _ItemPlan}: the winning lease attempts, decided before any event is applied."""
    lease_events = []
    for e in events:
        if e.type not in _PLANNED_TYPES or not (getattr(e, "hub", "") or ""):
            continue
        if lease_grantors is not None and e.actor not in lease_grantors.get(getattr(e, "hub", "") or "", ()):
            continue
        payload = e.payload if isinstance(e.payload, dict) else {}
        if _valid_item_id(payload) is None:
            continue
        seq = e.seq if type(e.seq) is int else -1
        lease_events.append(((_epoch_of(e, lease_grantors), seq, getattr(e, "log", None) or e.actor, e.hash), e))
    plans: dict = {}
    for order, e in sorted(lease_events, key=lambda t: t[0]):
        payload = e.payload
        plan = plans.setdefault(payload["id"], _ItemPlan())
        if e.type == "item.lease":
            holder, times = payload.get("holder"), _lease_times(payload)
            if not isinstance(holder, str) or not holder or times is None:
                continue
            key = _attempt_key(payload, e)
            if key in plan.attempts:
                plan.by_event[e.hash] = key          # a second grant of one attempt changes nothing
                continue
            att = _Attempt(key=key, holder=holder, start=times[0], end=times[1], epoch=order[0], order=order,
                           task_hash=payload.get("task_hash"), named="attempt" in payload)
            rival = next((w for w in plan.attempts.values()
                          if w.won and w.start < att.end and att.start < w.end), None)
            if rival is not None:
                att.won = False
                att.why = (f"lease to {holder!r} overlaps the lease held by {rival.holder!r}, which is earlier "
                           f"in grantor order; the earliest lease wins")
            plan.attempts[key] = att
            plan.by_event[e.hash] = key
        elif e.type == "item.lease.renew":
            att = plan.attempts.get(_renewed_key(payload, plan))
            expires = payload.get("expires_ms")
            renewed = payload.get("renewed_ms", att.start if att else None)
            if att is None:
                plan.outcome[e.hash] = "renews no known lease attempt"
            elif not att.won:
                plan.outcome[e.hash] = "a losing lease is never renewed into a winner"
            elif att.ended:
                plan.outcome[e.hash] = "the attempt has already ended"
            elif isinstance(expires, bool) or not isinstance(expires, int) or (
                    isinstance(renewed, int) and not isinstance(renewed, bool) and renewed >= att.end):
                plan.outcome[e.hash] = "renewal after the attempt expired (it does not resurrect it)"
            else:
                att.end = max(att.end, expires)
                plan.outcome[e.hash] = None
        elif e.type == "item.lease.expire":
            att = plan.attempts.get(payload.get("attempt") if isinstance(payload.get("attempt"), str) else "")
            if att is None or not att.won or att.ended:
                plan.outcome[e.hash] = "expires no live winning attempt"
                continue
            at = payload.get("expired_ms")
            if isinstance(at, int) and not isinstance(at, bool):
                att.end = min(att.end, max(at, att.start))
            att.ended = True
            frontier = payload.get("frontier")
            att.frontier = dict(frontier) if isinstance(frontier, dict) else {}
            plan.outcome[e.hash] = None
    return plans


def _renewed_key(payload: dict, plan: _ItemPlan) -> str:
    attempt = payload.get("attempt")
    if isinstance(attempt, str) and attempt:
        return attempt
    ref = payload.get("lease")
    return plan.by_event.get(ref.get("hash"), "") if isinstance(ref, dict) else ""


def _plan_of(state: "LedgerState", item_id: str) -> _ItemPlan | None:
    return getattr(state, "_lease_plan", {}).get(item_id)


def _lease_ref(event: Event, att: _Attempt | None = None) -> dict:
    ref = {"hub": getattr(event, "hub", "") or "", "log": getattr(event, "log", None) or event.actor,
           "seq": event.seq, "hash": event.hash, "hlc": event.hlc}
    if att is not None and att.named:
        ref.update(attempt=att.key, task_hash=att.task_hash, epoch=att.epoch, holder=att.holder)
    for key in ("class", "review"):
        if key in (event.payload or {}):
            ref[key] = event.payload[key]           # pinned to the attempt (Codex re-audit, new 5)
    role = (event.payload or {}).get("role")
    if isinstance(role, dict):
        # The role instructions this attempt was given, pinned by content hash
        # (amendment G): a later edit of the template is another attempt's.
        ref["role"] = {k: role.get(k) for k in ("name", "template", "sha256")}
    return ref


def _lease_times(payload: dict) -> tuple[int, int] | None:
    granted, expires = payload.get("granted_ms"), payload.get("expires_ms")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (granted, expires)) or expires < granted:
        return None
    return granted, expires


def lease_live(item: ItemState, now_ms: int) -> bool:
    """Does `item` carry a lease that is still live at `now_ms` (hub clock)?"""
    return (item.lease_holder is not None and item.status == "claimed"
            and item.lease_expires_ms is not None and now_ms < item.lease_expires_ms)


def _current_attempt(item: ItemState) -> str | None:
    return (item.lease_ref or {}).get("attempt") or (item.lease_ref or {}).get("hash")


def _handle_lease(state: LedgerState, event: Event) -> None:
    item = _get_item_or_orphan(state, event)
    if item is None or _dismissed(state, event, item):
        return
    payload = event.payload or {}
    if not getattr(event, "hub", ""):
        _note(item, event, "no-op (a lease is written only by a hub)")
        return
    plan = _plan_of(state, item.id)
    att = plan.attempts.get(plan.by_event.get(event.hash, "")) if plan else None
    if att is None:
        _note(item, event, "no-op (invalid lease payload)")
        return
    if not att.won:
        _lease_conflict(item, event, att.why)
        return
    if pool_role(item.payload) and not done_when_of(item.payload):
        # A POOL ITEM (amendment G): leased only with a DONE_WHEN. Which agent
        # may pick it up is the hub's judgement (a registered agent principal,
        # a role description that exists, the day's budget) and rests on
        # host files the fold cannot see; the hub refuses a bad grant. A grant
        # with no DONE_WHEN is a recorded conflict in every copy, never a lease.
        _lease_conflict(item, event, f"lease of a pool item with no DONE_WHEN to {att.holder!r}")
        return
    if item.status in ("completed", "submitted", "verified", "archived"):
        _note(item, event, f"no-op (lease illegal from status={item.status})")
        return
    if _current_attempt(item) == att.key:
        _note(item, event, "no-op (attempt already granted)")
        return
    if item.lease_holder is None and item.status != "created":
        _note(item, event, f"no-op (claimed outside a lease, owner={item.owner!r})")
        return
    previous = item.lease_holder
    _take_lease(item, att.holder, payload["expires_ms"], _lease_ref(event, att))
    item.pool_work = is_pool_item(item)
    _note(item, event, f"applied (leased to {att.holder!r}" + (
        f"; the previous attempt held by {previous!r} had ended)" if previous else ")"))


def _take_lease(item: ItemState, holder: str, expires: int, ref: dict) -> None:
    item.lease_holder, item.lease_expires_ms, item.lease_ref = holder, expires, ref
    item.status, item.owner = "claimed", holder
    item.granted_by = item.granted_at = None


def _handle_lease_renew(state: LedgerState, event: Event) -> None:
    item = _get_item_or_orphan(state, event)
    if item is None or _dismissed(state, event, item):
        return
    payload = event.payload or {}
    if not getattr(event, "hub", ""):
        _note(item, event, "no-op (a lease is renewed only by a hub)")
        return
    plan = _plan_of(state, item.id)
    why = plan.outcome.get(event.hash, "invalid renewal") if plan else "invalid renewal"
    if why:
        _note(item, event, f"no-op ({why})")
        return
    key = _renewed_key(payload, plan)
    if _current_attempt(item) != key or item.status != "claimed":
        _note(item, event, f"no-op (not the current lease attempt, holder={item.lease_holder!r})")
        return
    item.lease_expires_ms = max(item.lease_expires_ms or 0, payload["expires_ms"])
    _note(item, event, "applied (lease renewed)")


def _handle_lease_expire(state: LedgerState, event: Event) -> None:
    item = _get_item_or_orphan(state, event)
    if item is None or _dismissed(state, event, item):
        return
    plan = _plan_of(state, item.id)
    why = plan.outcome.get(event.hash, "invalid expiry") if plan else "invalid expiry"
    if why:
        _note(item, event, f"no-op ({why})")
        return
    key = (event.payload or {}).get("attempt")
    if _current_attempt(item) == key:
        payload = event.payload or {}
        item.lease_ref = {**item.lease_ref, "ended": True, "end_reason": payload.get("reason"),
                          "ended_ms": payload.get("expired_ms")}
        if item.status == "claimed" and item.lease_holder is not None:
            item.lease_holder = item.lease_expires_ms = None
            item.status, item.owner = "created", None
            _note(item, event, "applied (lease attempt ended; the item is back in the pool)")
            return
    _note(item, event, "applied (lease attempt ended)")


def _leased_result(state: LedgerState, item: ItemState, event: Event, what: str) -> bool:
    """For a result/release on an item with leases: True when it may proceed.

    Otherwise records why (conflict, or owner resolution) and returns False."""
    plan = _plan_of(state, item.id)
    if plan is None or not plan.attempts:
        if item.lease_ref is not None and event.actor != item.lease_holder:
            _lease_conflict(item, event, f"{what} by {event.actor!r}, who does not hold the lease "
                                         f"(holder {item.lease_holder!r})")
            return False
        return True
    payload = event.payload or {}
    named = payload.get("attempt")
    current = plan.attempts.get(_current_attempt(item) or "")
    if not isinstance(named, str) or not named:
        if current is not None and not current.named and event.actor == item.lease_holder:
            return True                                  # a lease from before attempts: the old holder rule
        _lease_conflict(item, event, f"{what} by {event.actor!r}, who does not hold the lease and names "
                                     f"no lease attempt (holder {item.lease_holder!r})")
        return False
    att = plan.attempts.get(named)
    if att is None:
        _lease_conflict(item, event, f"{what} by {event.actor!r} names an unknown lease attempt")
        return False
    if not att.won:
        _lease_conflict(item, event, f"{what} by {event.actor!r}, who does not hold the winning lease "
                                     f"(its attempt lost: {att.why})")
        return False
    if att.holder != event.actor:
        _lease_conflict(item, event, f"{what} by {event.actor!r}, who does not hold the lease "
                                     f"(attempt held by {att.holder!r})")
        return False
    if att.task_hash and payload.get("task_hash") != att.task_hash:
        _lease_conflict(item, event, f"{what} names other task content than its lease pinned")
        return False
    if att.ended and att.frontier is not None:
        chain = getattr(event, "log", None) or event.actor
        top = att.frontier.get(chain, -1)
        if not (type(event.seq) is int and type(top) is int and event.seq <= top):
            item.lease_unresolved.setdefault(event.hash, (
                f"{what} by {event.actor!r} for attempt {named} arrived after the grantor ended that "
                "attempt; the owner decides whether it stands"))
            _note(item, event, "recorded (after the attempt ended: owner resolution)")
            return False
    if item.lease_ref is None:
        _note(item, event, f"no-op ({what} sorts before its lease in this copy)")
        return False
    if (item.lease_ref or {}).get("attempt") != named:
        _lease_conflict(item, event, f"{what} by {event.actor!r} names attempt {named}, which is not the "
                                     "item's current attempt")
        return False
    return True


def _handle_lease_conflict(state: LedgerState, event: Event) -> None:
    """The hub's durable record of a conflict the fold found: idempotent."""
    item = _get_item_or_orphan(state, event)
    if item is None:
        return
    ref = (event.payload or {}).get("event") or {}
    key = ref.get("hash") if isinstance(ref, dict) else None
    if not isinstance(key, str) or not key:
        _note(item, event, "no-op (conflict names no event)")
        return
    item.lease_conflicts.setdefault(key, str((event.payload or {}).get("reason") or "recorded by the hub"))
    _note(item, event, "recorded (lease conflict)")


# ── ROLE POOLS (DIP-0034 October 2026 amendment, section G) ─────────────────
#
# The role is named in the task (owner decision, 2026-10-18). A task addressed
# to an agent is that agent's, role or not. A task with a role and NO assignee
# is a pool item: any available agent may lease it (the hub decides who is
# available), loads the role description and does the work; its completion
# needs a DONE_WHEN and counts only after another actor's check.

def _utc_day(hlc: str) -> str | None:
    """The UTC calendar day of an HLC stamp ("YYYY-MM-DD"), or None if unreadable."""
    import time
    try:
        ms = int(str(hlc).split(".", 1)[0])
        return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))
    except (ValueError, OverflowError, OSError):
        return None


def utc_day_ms(ms: int) -> str:
    import time
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def done_when_of(payload: dict) -> str:
    """An item's DONE_WHEN: `done_when` in its payload, or the org property."""
    payload = payload or {}
    dw = payload.get("done_when")
    if not dw:
        props = (payload.get("org") or {}).get("properties") if isinstance(payload.get("org"), dict) else None
        dw = (props or {}).get("DONE_WHEN") if isinstance(props, dict) else None
    return dw.strip() if isinstance(dw, str) else ""


def pool_role(payload) -> str | None:
    """The role of a pool item: a `role` and no `assignee`. None for anything else."""
    payload = payload or {}
    role = payload.get("role")
    return role if isinstance(role, str) and role and not payload.get("assignee") else None


def is_pool_item(item) -> bool:
    """A pool item that a hub has leased (the only kind pool rules touch).

    The class the lease attempt PINNED decides when it names one (Codex
    re-audit, new defect 5): a holder that re-addresses the task to itself
    during its attempt does not turn submitted-for-review work into a plain
    completion. Leases from before the pin keep the task's own fields.

    Pool work stays pool work (Codex round-3 verdict, `pool_release_reclassify`):
    once created or leased as a pool task, a later re-addressing -- and so an
    `addressed` attempt -- does not make its completion a plain one."""
    ref = getattr(item, "lease_ref", None)
    if ref is not None and getattr(item, "pool_work", False):
        return True
    if isinstance(ref, dict) and ref.get("class") in ("pool", "addressed"):
        return ref["class"] == "pool"
    return bool(pool_role(getattr(item, "payload", None))) and ref is not None


def result_digest(payload) -> str:
    """The digest a pool submission records: sha256 over the canonical
    {result, evidence} of the completion (amendment G)."""
    import hashlib
    from .events import canonical_bytes
    p = payload if isinstance(payload, dict) else {}
    return hashlib.sha256(canonical_bytes({"result": p.get("result"), "evidence": p.get("evidence")})).hexdigest()


def pool_refusal(item, etype: str, actor: str, payload=None) -> str | None:
    """Why the fold will not count this completion or check of a pool item.

    "code: why". The fold sees writer names only; that the checker is another
    EXECUTION IDENTITY (another principal) is `hub_policy.authorize`'s rule,
    applied by the hub and by `fold(..., policy=)`."""
    if item is None or not is_pool_item(item):
        return None
    p = payload if isinstance(payload, dict) else {}
    if etype == "item.complete":
        if not done_when_of(item.payload):
            return "no_done_when: a pool item's completion needs a DONE_WHEN"
        if "result_digest" in p and p["result_digest"] != result_digest(p):
            return "bad_digest: the completion's result_digest does not match its result and evidence"
        return None
    if etype == "item.verify":
        from actor_identity import base_writer
        holder = (item.submission or {}).get("holder") or item.lease_holder
        if holder and base_writer(actor) == base_writer(holder):
            return "own_check: the lease holder cannot check its own work; another actor must"
        sub = item.submission
        if sub is not None:
            for key in ("attempt", "task_hash", "result_digest"):
                if p.get(key) != sub.get(key):
                    what = "result digest" if key == "result_digest" else key.replace("_", " ")
                    return (f"unbound: the check names another {what} than the submission it would accept "
                            f"(submitted {key} {str(sub.get(key))[:16]})")
    return None


def pool_accepted(item) -> bool:
    """A pool item is done only once another execution identity checked its submission."""
    return is_pool_item(item) and item.status == "verified"


def pool_budget(state: LedgerState, hub: str, grantor: str, role: str) -> int | None:
    b = state.budgets.get(f"{hub}|{grantor}|{role}")
    return None if b is None else b["cents_per_day"]


def pool_spent(state: LedgerState, role: str, now_ms: int) -> int:
    return state.pool_spend.get(f"{role}|{utc_day_ms(now_ms)}", 0)


def _handle_pool_budget(state: LedgerState, event: Event) -> None:
    p = event.payload or {}
    role, cap = p.get("role"), p.get("cents_per_day")
    if (not getattr(event, "hub", "") or not isinstance(role, str) or not role
            or isinstance(cap, bool) or not isinstance(cap, int) or cap < 0):
        state.orphans.append(f"{event.hlc} pool.budget invalid")
        return
    state.budgets[f"{event.hub}|{event.actor}|{role}"] = {"cents_per_day": cap, "space": p.get("space")}

_LEASE_TYPES = frozenset({"item.lease", "item.lease.renew", "item.lease.expire", "item.lease.conflict"})
_PLANNED_TYPES = frozenset({"item.lease", "item.lease.renew", "item.lease.expire"})

_HANDLERS = {
    "pool.budget": _handle_pool_budget,
    "item.lease": _handle_lease,
    "item.lease.renew": _handle_lease_renew,
    "item.lease.conflict": _handle_lease_conflict,
    "item.lease.expire": _handle_lease_expire,
    "item.create": _handle_create,
    "item.claim": _handle_claim,
    "item.grant": _handle_grant,
    "item.release": _handle_release,
    "item.complete": _handle_complete,
    "item.update": _handle_update,
    "item.verify": _handle_verify,
    "item.dismiss": _handle_dismiss,
    "item.reopen": _handle_reopen,
    "item.archive": _handle_archive,
    "owner.set": _handle_owner_set,
    "spend.record": _handle_spend,
}
