#!/usr/bin/env python3
"""Record the authored Org value for a field the ledger disagrees on.

`sync_generated` performs a three-way merge between the projection base, the
Org file, and the ledger. When base has no value and the two sides each added a
DIFFERENT one, that is a genuine conflict and both ingest and projection stop --
correctly, because neither side is automatically right.

But it does not clear by itself, and the deadlock is easy to fall into: ingest
writes the ledger's value, someone edits the Org file before the projection
runs, the projection refuses, and so the base is never advanced past the point
where the two agreed. Every later cycle re-derives the same conflict from the
same stale base. Measured 2026-09-16: two spaces stuck on one renamed filename
inside a KEY_FILES property, with the hourly cycle red for both.

This is the operator's "the Org file is right" answer, written as an ordinary
conditional `item.update` whose precondition is the ledger's CURRENT value --
so it is refused, not silently applied, if the ledger moves underneath it. It
never touches the Org file, and it never guesses which side is right.

    ledger_accept_authored.py --space 0-personal --item <id> --field org.properties.KEY_FILES
    ledger_accept_authored.py --space 0-personal --item <id> --field org --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))
from ledger.edits import conditional_payload  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.projection_state import snapshot  # noqa: E402


def dig(value, path: list[str]):
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def authored(space: Path, item_id: str) -> dict:
    text = (space / 'org/next_actions.org').read_text(encoding='utf-8')
    fields = snapshot(text, space.name)['items'].get(item_id)
    if fields is None:
        raise SystemExit(f'{item_id} is not in {space}/org/next_actions.org')
    return fields


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--space', required=True, type=Path)
    ap.add_argument('--item', required=True)
    ap.add_argument('--field', required=True,
                    help="top-level payload key, optionally dotted for reporting "
                         "(e.g. 'org' or 'org.properties.KEY_FILES')")
    ap.add_argument('--actor', default=None)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args(argv)
    # Identity at startup, strictly (owner follow-up Q2); --actor wins.
    if not a.actor:
        from actor_identity import UndeclaredActor, this_actor
        try:
            a.actor = this_actor(strict=True)
        except UndeclaredActor as exc:
            print(f'REFUSED: {exc}', file=sys.stderr)
            return 2

    space = a.space.resolve()
    path = a.field.split('.')
    top = path[0]
    item = fold(read_events(space)).items.get(a.item)
    if item is None:
        raise SystemExit(f'{a.item} is not in the ledger for {space.name}')
    want = authored(space, a.item)
    if top not in want:
        raise SystemExit(f'the Org file carries no {top!r} for {a.item}')

    print(f'{space.name} {a.item} {a.field}')
    print(f'  ledger  : {str(dig(item.payload, path))[:160]}')
    print(f'  authored: {str(dig(want, path))[:160]}')
    if dig(item.payload, path) == dig(want, path):
        print('  already agree; nothing to record')
        return 0
    if not a.apply:
        print('  dry run -- pass --apply to record the authored value')
        return 0

    log = EventLog(space, a.actor)
    payload = conditional_payload(item, {top: want[top]})
    event = log.append('item.update', payload)
    after = fold(read_events(space)).items[a.item]
    if event.hash in after.edit_conflicts:
        raise SystemExit('refused: the ledger changed underneath this edit; re-run')
    print(f'  recorded {event.hash[:12]} seq {event.seq}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
