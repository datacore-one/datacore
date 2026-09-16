#!/usr/bin/env python3
"""Clear a retained edit conflict on a DISMISSED item, by the ledger's own route.

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

The precondition is the safety. `apply_condition` demands the item's ENTIRE
payload still equal the recorded base, plus matching status and owner, so this
cannot run against an item that moved underneath it -- and it is checked here,
before anything is appended, so a refusal costs no event.

    ledger_resolve_conflict.py --space 0-personal --item <id>
    ledger_resolve_conflict.py --space 0-personal --item <id> --apply
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
    ap.add_argument('--item', required=True)
    ap.add_argument('--actor', default=None)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args(argv)

    space = a.space.resolve()
    item = fold(read_events(space)).items.get(a.item)
    if item is None:
        raise SystemExit(f'{a.item} is not in the ledger for {space.name}')
    if not item.edit_conflicts:
        print('no retained edit conflicts; nothing to reconcile')
        return 0
    if item.status != 'dismissed':
        raise SystemExit(f'item status is {item.status!r}; this route is only for a dismissed item')

    print(f'{space.name} {a.item} — {len(item.edit_conflicts)} retained conflict(s):')
    for digest, message in item.edit_conflicts.items():
        print(f'  {digest[:12]}  {message}')

    # Resend the item's OWN recorded closure, changing nothing.
    fields = {'kind': item.closed_kind, 'reason': item.closed_reason}
    payload = conditional_payload(item, fields, terminal=True,
                                  resolves=list(item.edit_conflicts))
    try:
        apply_condition(item, payload)
    except EditConflict as exc:
        raise SystemExit(f'refused before writing anything: {exc}')
    print('  precondition holds; the dismissal itself changes nothing')

    if not a.apply:
        print('  dry run — pass --apply to record the reconciliation')
        return 0

    from actor_identity import this_actor
    log = EventLog(space, a.actor or this_actor())
    event = log.append('item.dismiss', payload)
    after = fold(read_events(space)).items[a.item]
    if after.edit_conflicts:
        raise SystemExit(f'conflicts remain after {event.hash[:12]}: {after.edit_conflicts}')
    print(f'  reconciled by {event.hash[:12]} seq {event.seq}; conflicts cleared')
    return 0


if __name__ == '__main__':
    sys.exit(main())
