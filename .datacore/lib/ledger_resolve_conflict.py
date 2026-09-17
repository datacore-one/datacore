#!/usr/bin/env python3
"""Clear a retained edit conflict, by the ledger's own route.

When a conditional edit is refused, `fold` keeps the refusal on the item as
`edit_conflicts[<event hash>]` and every later ingest of that space stops with
"unresolved replicated edits; preserve the file and reconcile event conflicts".
That is deliberate -- the refused intent is retained rather than dropped -- but
on a DISMISSED item it had no exit:

  * a conditional `item.update` is rejected before it can resolve anything
    (`update_payload` raises "conditional edit targets a terminal item"), and
  * claim/grant/complete/verify become no-ops while any conflict is present.

The one path that both reaches a dismissed item and clears `resolves` is
`item.dismiss` with a TERMINAL precondition, which `_handle_dismiss` applies
before the already-dismissed no-op. That is what this emits. The dismissal
itself changes nothing: the item is dismissed already, and the fields are its
own recorded closure, resent unchanged.

A LIVE item (created/claimed/completed/verified) has the same dead end for the
same reason -- claim/grant/complete/verify are no-ops while a conflict is
present -- and its exit is a conditional `item.update` carrying `resolves`.
Measured 2026-09-17: a nightshift run left one conflict on a completed item in
5-plur, and `project` then refused the WHOLE space ("unresolved replicated
edits"), so the hourly cycle and every later run stopped. The update rewrites
the item's own payload over itself, pinned field by field to what the ledger
currently holds, so it cannot run against an item that moved and it changes
nothing when it runs.

The precondition is the safety. `apply_condition` demands the item's ENTIRE
payload still equal the recorded base, plus matching status and owner, so this
cannot run against an item that moved underneath it -- and it is checked here,
before anything is appended, so a refusal costs no event.

    ledger_resolve_conflict.py --space 0-personal --item <id>
    ledger_resolve_conflict.py --space 0-personal --item <id> --apply
    ledger_resolve_conflict.py --space 5-plur --all          # every stuck item
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))
from ledger.edits import EditConflict, apply_condition, conditional_payload  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--space', required=True, type=Path)
    ap.add_argument('--item')
    ap.add_argument('--all', action='store_true',
                    help='every item in the space that holds a retained conflict')
    ap.add_argument('--actor', default=None)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args(argv)

    space = a.space.resolve()
    if bool(a.item) == bool(a.all):
        raise SystemExit('pass either --item <id> or --all')
    state = fold(read_events(space))
    if a.all:
        targets = [i for i, it in sorted(state.items.items()) if it.edit_conflicts]
        if not targets:
            print(f'{space.name}: no retained edit conflicts; nothing to reconcile')
            return 0
    else:
        if a.item not in state.items:
            raise SystemExit(f'{a.item} is not in the ledger for {space.name}')
        targets = [a.item]

    refused = 0
    for identity in targets:
        if _reconcile(space, identity, a.actor, a.apply) != 0:
            refused += 1
    return 1 if refused else 0


def _reconcile(space: Path, identity: str, actor: str | None, apply: bool) -> int:
    item = fold(read_events(space)).items[identity]
    if not item.edit_conflicts:
        print(f'{space.name} {identity} — no retained edit conflicts; nothing to reconcile')
        return 0

    print(f'{space.name} {identity} — {len(item.edit_conflicts)} retained conflict(s), '
          f'status {item.status}:')
    for digest, message in item.edit_conflicts.items():
        print(f'  {digest[:12]}  {message}')

    resolves = list(item.edit_conflicts)
    if item.status == 'dismissed':
        # Resend the item's OWN recorded closure, changing nothing.
        kind, payload_fields = 'item.dismiss', {'kind': item.closed_kind, 'reason': item.closed_reason}
        payload = conditional_payload(item, payload_fields, terminal=True, resolves=resolves)
        changes_nothing = 'the dismissal itself changes nothing'
    else:
        # A live item: rewrite its payload over itself, pinned field by field.
        kind = 'item.update'
        payload = conditional_payload(item, {k: v for k, v in item.payload.items() if k != 'id'},
                                      resolves=resolves)
        changes_nothing = 'the update writes the item its own current values'
    try:
        apply_condition(item, payload)
    except EditConflict as exc:
        print(f'  REFUSED before writing anything: {exc}')
        return 1
    print(f'  precondition holds; {changes_nothing}')

    if not apply:
        print('  dry run — pass --apply to record the reconciliation')
        return 0

    from actor_identity import this_actor
    log = EventLog(space, actor or this_actor())
    event = log.append(kind, payload)
    after = fold(read_events(space)).items[identity]
    if after.edit_conflicts:
        print(f'  conflicts remain after {event.hash[:12]}: {sorted(after.edit_conflicts)}')
        return 1
    print(f'  reconciled by {event.hash[:12]} seq {event.seq}; conflicts cleared')
    return 0


if __name__ == '__main__':
    sys.exit(main())
