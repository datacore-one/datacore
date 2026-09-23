#!/usr/bin/env python3
"""Reconcile authored next_actions.org before enabling generated projections.

Only Phase 0 source edits are eligible. An absent ID or similar title never
proves that a ledger item is disposable: unmatched items remain intact and
will be added to the projection. Destructive deduplication requires an
explicit, separately reviewed operator decision with retained history.

Dry-run by default. Conditional edits require every active reader to have
been upgraded and ledger-edit-protocol=1 enabled before --apply. With
ledger-edit-protocol=2 a changed field is detected type-strictly (canonical
JSON bytes: 1, True and 1.0 differ) and the planned edits carry version 2
(owner follow-up Q3); under 1 the comparison stays Python `==`.

The writer is `--actor`, else this machine's declared actor, resolved with
`actor_identity.this_actor(strict=True)` at startup; an undeclared host is
refused before anything is read (owner follow-up Q2).
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import sys

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))
from ledger.edits import EditConflict, conditional_payload, update_payload  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.projector import project  # noqa: E402
from ledger.projection_state import _same, changed_fields, edit_strict, snapshot  # noqa: E402
from org_transaction import serialized, watch_file  # noqa: E402


@serialized
def plan(space: Path) -> dict:
    marker = watch_file(space / '.datacore/ledger-phase')['before']
    if marker is not None and marker.strip() != '0':
        raise EditConflict('preparation requires authored Phase 0; generated edits must use normal ingestion')
    text = watch_file(space / 'org/next_actions.org')['before']
    if text is None:
        raise EditConflict('authored next_actions.org is missing; preserve the ledger')
    state = fold(read_events(space))
    # Protocol 2 (owner follow-up Q3): a changed field is detected type-strictly,
    # so an authored 1 -> True is planned, and the edit carries version 2.
    strict = edit_strict(space)
    version = 2 if strict else 1
    source = snapshot(text, space.name)['items']
    rendered = snapshot(project(state, space=space.name).text, space.name)['items']
    events = []
    for identity, fields in source.items():
        item = state.items.get(identity)
        if item is None:
            raise EditConflict('source heading is not in the ledger; ingest before preparation')
        if identity not in rendered:
            raise EditConflict('source contains a nonprojectable item; preserve and reconcile its history explicitly')
        changes = changed_fields({key: value for key, value in fields.items() if key != 'created'},
                                 rendered[identity], strict=strict)
        if not changes:
            continue
        if item.status != 'created':
            raise EditConflict('cannot automatically edit an item whose execution or closure has started')
        terminal = changes.get('state') in ('DONE', 'CANCELLED')
        kind = 'done' if changes.get('state') == 'DONE' else 'dropped'
        if terminal:
            changes.pop('state')
        expected = deepcopy(item)
        if changes:
            update = conditional_payload(item, changes, version=version)
            events.append({'type': 'item.update', 'payload': update})
            expected.payload = update_payload(item, update)
        if terminal:
            events.append({'type': 'item.dismiss', 'payload': conditional_payload(expected,
                {'kind': kind, 'reason': 'explicit terminal state in authored Phase 0 Org'}, terminal=True,
                version=version)})
    return {'version': 1, 'state_root': state.state_root(),
            'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'events': events, 'unmatched': sorted(set(rendered) - set(source))}


@serialized
def apply(space: Path, actor: str, proposed: dict) -> int:
    # Check the full plan again, including source bytes and event state. A
    # caller cannot silently apply an old plan after either side changed.
    if not _same(plan(space), proposed, edit_strict(space)):
        raise EditConflict('preparation plan is stale; review a fresh plan')
    log = EventLog(space, actor)
    for event in proposed['events']:
        log.append(event['type'], event['payload'])
    # A concurrent append can still arrive on another writer's chain. Its
    # preconditions preserve both proposals and make the conflict explicit.
    if any(item.edit_conflicts for item in fold(read_events(space)).items.values()):
        raise EditConflict('concurrent preparation conflict; proposals retained for explicit reconciliation')
    return len(proposed['events'])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--root', type=Path, default=Path(os.environ.get('DATACORE_ROOT', Path.home() / 'Data')))
    ap.add_argument('--space', required=True)
    ap.add_argument('--actor', default=None,
                    help='writer name (default: this machine\'s declared actor, resolved strictly)')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args(argv)
    # Resolve identity AT STARTUP and refuse on an undeclared host (owner
    # follow-up Q2): never a hostname guess, never discovered only at append.
    # An explicit --actor is the caller's choice and is not resolved at all.
    if not args.actor:
        from actor_identity import UndeclaredActor, this_actor
        try:
            args.actor = this_actor(strict=True)
        except UndeclaredActor as exc:
            print(f'REFUSED: {exc}', file=sys.stderr)
            return 2
    space = args.root / args.space
    try:
        proposed = plan(space)
        print(f"  {args.space}: {len(proposed['events'])} planned event(s); "
              f"{len(proposed['unmatched'])} unmatched ledger item(s) preserved")
        if not args.apply:
            print('  dry run — review source changes, then re-run with --apply')
            return 0
        count = apply(space, args.actor, proposed)
    except (EditConflict, OSError, UnicodeError) as exc:
        print(f'REFUSED: {exc}', file=sys.stderr)
        return 1
    print(f'  appended {count} event(s) as {args.actor}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
