#!/usr/bin/env python3
"""Show HOW an item's fingerprint changes across the checkpoint round-trip."""
import os
import sys
import tempfile
from pathlib import Path

LIB = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('DATACORE_ROOT', str(Path.home() / 'Data')))
sys.path.insert(0, str(LIB))

from ledger.fold import fold
from ledger.genesis import import_space
from ledger.log import read_events
from ledger.projector import project
import ledger_checkpoint as lc

FIELDS = ['title', 'state', 'tags', 'scheduled', 'deadline']

for space_name in sys.argv[1:] or ['2-datacore']:
    space = ROOT / space_name
    state = fold(read_events(space))
    live = lc._fingerprint(state)
    fresh = project(state, space=space.name).text
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / space.name
        (scratch / '.datacore' / 'events').mkdir(parents=True)
        (scratch / 'org').mkdir()
        (scratch / 'org' / 'next_actions.org').write_text(fresh, encoding='utf-8')
        import_space(scratch, org_file=scratch / 'org' / 'next_actions.org')
        restored = lc._fingerprint(fold(read_events(scratch)))

    for iid in sorted(set(live) & set(restored)):
        if live[iid] != restored[iid]:
            print(f'=== {space_name} ITEM {iid}')
            for a, b, label in zip(live[iid], restored[iid], FIELDS):
                if a != b:
                    print(f'  {label}:\n    live     = {a!r}\n    restored = {b!r}')
            blocks = [b for b in fresh.split('\n* ') if iid in b]
            if blocks:
                print('  --- projected block (truncated) ---')
                print('  ' + ('* ' + blocks[0][:900]).replace('\n', '\n  '))
