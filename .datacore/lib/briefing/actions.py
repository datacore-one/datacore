"""Briefing action materialization: turn briefing items into ledger items.

Datacore v2 Phase 5. This module is what makes "dismissed means gone
forever" mechanical rather than a convention someone has to remember.

Every briefing item is reduced to a stable `item_id` -- a hash of its
NORMALIZED text (lowercase, whitespace runs collapsed to one space,
stripped). Two runs of the briefing pipeline that describe the same
underlying thing, even if the exact wording drifts slightly between runs,
therefore land on the SAME id. `materialize` folds the space's ledger
once up front and skips any item whose id is already present in
`state.items` -- with ANY status, including "dismissed". That single rule
is the entire resurrection guard: once a human dismisses an item, its id
is permanently "known" to the fold, so no future `materialize` call ever
re-appends its `item.create`, no matter how the briefing rephrases it.

`materialize` never lets a single item's `PolicyError` (a side-effect
item -- one whose `effects` intersect the policy's `cosign_effects` --
with no valid `approval_ref`) abort the whole call. Each item is
attempted independently; a rejection is collected into
`MaterializeResult.blocked` and the loop moves on. This matters because a
briefing typically bundles many unrelated items in one call: one item
waiting on a human grant must not hold the rest hostage.

Two dedupe layers combine inside one call:
  1. Ids already known to the up-front fold (from a PRIOR call, or a
     dismissal) -- these are the resurrection guard.
  2. Ids that appear more than once WITHIN this same call (two items that
     normalize to the same text) -- the up-front fold can't see these
     since nothing's been appended yet, so they're tracked in a local
     set as the loop goes. Whichever occurrence is seen first "wins" (is
     attempted); every later occurrence of the same id, in the same
     call, is skipped outright -- regardless of whether the first
     occurrence ended up created or blocked, since attempting the same
     id twice in one call is never useful for a caller's purposes here.

`act` is the read side of an item's lifecycle after creation: `claim`,
`complete`, `dismiss` are plain, ungated `item.*` appends -- per
`ledger.policy`, only `item.create` is ever gated, so downstream
lifecycle transitions go straight through `EventLog.append`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ledger.events import Event
from ledger.fold import fold
from ledger.log import EventLog, read_events
from ledger.policy import Policy, PolicyError, guarded_append

_ACTION_EVENT_TYPES = {
    "claim": "item.claim",
    "complete": "item.complete",
    "dismiss": "item.dismiss",
}


def item_id(text: str) -> str:
    """Stable id for a briefing item's TEXT: sha256 hex digest, truncated
    to 16 hex characters, of the normalized form of `text`.

    Normalize = lowercase, collapse any run of whitespace to a single
    space, strip leading/trailing whitespace. `"  Buy   Milk\\n"` and
    `"buy milk"` therefore produce the same id -- that equivalence IS the
    never-resurface guarantee `materialize` relies on: the same
    underlying item, reworded slightly across briefing runs, is still
    recognized as "already handled".
    """
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


@dataclass
class MaterializeResult:
    """Outcome of one `materialize` call.

    - `created`: the `Event`s actually appended (one per item that was
      neither already-known nor an in-call duplicate nor policy-blocked).
    - `skipped`: `item_id`s that were NOT (re-)created -- either because
      the up-front fold already had that id (any status, including
      dismissed), or because an earlier item in this same call already
      claimed that id.
    - `blocked`: `(item_text, error_message)` pairs for items that raised
      `PolicyError` (a side-effect item with no valid grant). The
      ORIGINAL item text is kept here (not the id) so a human reviewing
      the result can see which item needs a grant, in its own words.
    """

    created: list[Event] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    blocked: list[tuple[str, str]] = field(default_factory=list)


def materialize(
    items: list[dict],
    space_dir: Path,
    actor: str,
    policy: Policy | None = None,
) -> MaterializeResult:
    """Turn `items` (each `{"text": str, "effects": [...]?}`) into ledger
    `item.create` events, one per item, skipping anything already known.

    Folds `read_events(space_dir)` exactly ONCE, up front -- not once per
    item -- so the whole call sees one consistent snapshot of existing
    item ids. For each item, in order:

      1. Compute `tid = item_id(item["text"])`.
      2. If `tid` is already in the up-front fold's `state.items` (any
         status) OR has already been claimed earlier in THIS call, skip:
         `tid` is appended to `result.skipped` and nothing is written.
      3. Otherwise, build `{"id": tid, "title": item["text"], "effects":
         item.get("effects", [])}` (plus `approval_ref`, forwarded
         through when the item carries one -- needed for a side-effect
         item backed by an existing grant) and append it via
         `guarded_append`, so a side-effecting item with no valid grant
         raises `PolicyError` rather than silently creating an ungated
         item.
      4. A `PolicyError` from step 3 is caught HERE, not propagated: it's
         recorded as `(item["text"], str(exc))` in `result.blocked`, and
         the loop continues to the next item. One blocked item must never
         stop the rest of the call from materializing.

    `title` is always the item's ORIGINAL, un-normalized text -- `tid`'s
    normalization is purely a dedupe key, never what gets displayed/stored
    as the item's title.

    `policy` is forwarded to `guarded_append` (which defaults to
    `load_policy()` itself when `None`).
    """
    state = fold(read_events(space_dir))
    seen_ids: set[str] = set(state.items.keys())

    log = EventLog(space_dir, actor)
    result = MaterializeResult()

    for item in items:
        text = item["text"]
        tid = item_id(text)

        if tid in seen_ids:
            result.skipped.append(tid)
            continue
        seen_ids.add(tid)

        payload = {"id": tid, "title": text, "effects": item.get("effects", [])}
        approval_ref = item.get("approval_ref")
        if approval_ref:
            payload["approval_ref"] = approval_ref
        # `check` is the assertion that proves the item was actually done. It
        # must survive into the payload or a dispatcher has nothing to verify
        # against and can only trust the agent's prose -- which reads as
        # confident completion even when the agent declined. Forwarded like
        # approval_ref rather than added to the fixed shape, so an item without
        # one is unchanged.
        check = item.get("check")
        if check:
            payload["check"] = check

        try:
            event = guarded_append(log, "item.create", payload, policy=policy, space_dir=space_dir)
        except PolicyError as exc:
            result.blocked.append((text, str(exc)))
            continue

        result.created.append(event)

    return result


def act(space_dir: Path, item_id: str, action: str, actor: str) -> Event:
    """Append the `item.*` event for `action` (one of `claim`, `complete`,
    `dismiss`) against `item_id`, via a plain (ungated) `EventLog` -- only
    `item.create` is ever policy-gated (per `ledger.policy`), so lifecycle
    transitions on an already-created item go straight through.

    Raises `ValueError` for any `action` outside `{claim, complete,
    dismiss}` -- nothing is appended in that case.
    """
    event_type = _ACTION_EVENT_TYPES.get(action)
    if event_type is None:
        raise ValueError(
            f"unknown action: {action!r} (expected one of {sorted(_ACTION_EVENT_TYPES)})"
        )

    log = EventLog(space_dir, actor)
    return log.append(event_type, {"id": item_id})
