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

`item.dismiss` is terminal: once an item's status is "dismissed", every
later event addressed to that item id -- including the `owner.set` admin
override -- is a history no-op. Nothing can revive a dismissed item.

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

from dataclasses import dataclass, field

from .events import Event


@dataclass
class ItemState:
    id: str
    title: str
    owner: str | None
    status: str  # created | claimed | completed | verified | dismissed
    history: list[str] = field(default_factory=list)


@dataclass
class LedgerState:
    items: dict[str, ItemState] = field(default_factory=dict)
    spend: dict[str, int] = field(default_factory=dict)
    orphans: list[str] = field(default_factory=list)


def fold(events: list[Event]) -> LedgerState:
    """Reduce `events` (already merged + hlc-sorted) into a `LedgerState`.

    Pure and order-preserving: does not sort, does not mutate `events` or
    any `Event`/payload within it, and produces byte-for-byte identical
    output for identical input on every call.
    """
    state = LedgerState()

    for event in events:
        handler = _HANDLERS.get(event.type)
        if handler is not None:
            handler(state, event)
        # Event types outside this fold's scope (metric.attest,
        # artifact.attest, policy.set, ...) are silently ignored here --
        # they don't affect item/ownership/spend state.

    return state


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
    return item


def _dismissed(state: LedgerState, event: Event, item: ItemState) -> bool:
    """If `item` is already terminal, note the no-op and report True.

    Called first by every handler except `item.create` (a duplicate create
    against a dismissed item is still just a no-op, but the "item already
    exists" framing there covers it) so that no later event -- including
    the `owner.set` admin override -- can revive or alter a dismissed item.
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
    item.status = "claimed"
    _note(item, event, "applied")


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
    item.owner = None
    item.status = "created"
    _note(item, event, "applied")


def _handle_complete(state: LedgerState, event: Event) -> None:
    item = _get_item_or_orphan(state, event)
    if item is None or _dismissed(state, event, item):
        return
    if item.status != "claimed":
        _note(item, event, f"no-op (illegal transition from status={item.status})")
        return
    item.status = "completed"
    _note(item, event, "applied")


def _handle_verify(state: LedgerState, event: Event) -> None:
    item = _get_item_or_orphan(state, event)
    if item is None or _dismissed(state, event, item):
        return
    if item.status != "completed":
        _note(item, event, f"no-op (illegal transition from status={item.status})")
        return
    item.status = "verified"
    _note(item, event, "applied")


def _handle_dismiss(state: LedgerState, event: Event) -> None:
    item = _get_item_or_orphan(state, event)
    if item is None or _dismissed(state, event, item):
        return
    item.status = "dismissed"
    _note(item, event, "applied")


def _handle_owner_set(state: LedgerState, event: Event) -> None:
    item = _get_item_or_orphan(state, event)
    if item is None or _dismissed(state, event, item):
        return
    item.owner = event.payload.get("owner")
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


_HANDLERS = {
    "item.create": _handle_create,
    "item.claim": _handle_claim,
    "item.release": _handle_release,
    "item.complete": _handle_complete,
    "item.verify": _handle_verify,
    "item.dismiss": _handle_dismiss,
    "owner.set": _handle_owner_set,
    "spend.record": _handle_spend,
}
