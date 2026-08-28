#!/usr/bin/env python3
"""task_cleanup.py — apply the mechanical (safe) fixes from task_audit.py.

Three buckets, dry-run by default:

  zombies   Open tasks whose EXACT normalized heading matches an already
            closed task anywhere. Closed with the twin's state and a
            CLOSED_REASON pointing at the twin.
            Guards: only TODO/NEXT/WAITING/REVIEW (never WORKING/QUEUED),
            normalized heading >= 20 chars, and scheduled date absent or
            >30 days past (protects live recurring templates).

  dup-ids   The same :ID: present in two files. The non-canonical copy
            (canonical order: next_actions > inbox > nightshift > archive)
            gets a fresh ID; if it is an open exact-heading duplicate it is
            also CANCELLED as a routing copy.

  digests   Perishable digest tasks: nightshift daily digests (all but the
            newest closed as superseded) and stale "Daily News Digest"
            tasks (all closed as expired).

Usage:
    python3 .datacore/lib/task_cleanup.py [zombies|dup-ids|digests|all] [--apply]

Every change carries CLOSED_REASON/ID_REASSIGNED provenance. Re-run
task_audit.py afterwards to measure the delta.
"""
import argparse
import glob
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from org_workspace import OrgWorkspace  # noqa: E402
from task_audit import normalize, parse_date, OPEN_STATES  # noqa: E402

CLOSABLE = {'TODO', 'NEXT', 'WAITING', 'REVIEW'}
TWIN_CLOSED = {'DONE': 'DONE', 'COMPLETED': 'DONE', 'CANCELLED': 'CANCELLED'}
CANON_ORDER = ['next_actions.org', 'inbox.org', 'nightshift.org']
TODAY = date.today()
STAMP = str(TODAY)


def canon_rank(path):
    name = Path(path).name
    if 'archive' in name:
        return 90
    try:
        return CANON_ORDER.index(name)
    except ValueError:
        return 50


def scan():
    """One read pass over every org file → task dicts (audit-compatible)."""
    tasks = []
    for f in sorted(glob.glob('[0-9]-*/org/*.org')):
        try:
            ws = OrgWorkspace()
            ws.load(f)
            nodes = list(ws.all_nodes())
        except Exception:
            continue
        for n in nodes:
            state = getattr(n, 'todo', None)
            if not state:
                continue
            try:
                props = dict(n.properties or {})
            except Exception:
                props = {}
            heading = str(getattr(n, 'heading', '') or '')
            tasks.append({
                'file': f, 'space': f.split('/')[0],
                'archive': 'archive' in Path(f).name,
                'state': state, 'heading': heading,
                'norm': normalize(heading),
                'id': props.get('ID'),
                'scheduled': parse_date(getattr(n, 'scheduled', None)),
            })
    return tasks


def plan_zombies(tasks):
    closed_by_norm = {}
    for t in tasks:
        if t['state'] in TWIN_CLOSED and len(t['norm']) >= 20:
            closed_by_norm.setdefault(t['norm'], t)
    actions = []
    for t in tasks:
        if t['archive'] or t['state'] not in CLOSABLE or len(t['norm']) < 20:
            continue
        if Path(t['file']).name == 'habits.org':
            continue  # recurring-habit definitions — never auto-close
        if t['scheduled'] and (TODAY - t['scheduled']).days <= 30:
            continue
        twin = closed_by_norm.get(t['norm'])
        if not twin or twin is t or not t['id']:
            continue
        actions.append({
            'kind': 'close', 'file': t['file'], 'id': t['id'],
            'state': TWIN_CLOSED[twin['state']],
            'reason': (f"exact duplicate of {twin['state']} twin in {twin['file']}"
                       f" — task-audit cleanup {STAMP}"),
            'heading': t['heading'][:70],
        })
    return actions


def plan_dup_ids(tasks):
    by_id = defaultdict(list)
    for t in tasks:
        if t['id']:
            by_id[t['id']].append(t)
    actions = []
    for tid, copies in by_id.items():
        files = {c['file'] for c in copies}
        if len(files) < 2:
            continue
        copies.sort(key=lambda c: canon_rank(c['file']))
        canonical, rest = copies[0], copies[1:]
        for i, c in enumerate(rest):
            if c['file'] == canonical['file']:
                continue
            # never touch 6-meridian while its merge is held
            if c['space'] == '6-meridian':
                if canonical['space'] != '6-meridian':
                    canonical, c = c, canonical  # edit the other side instead
                else:
                    continue
            new_id = f'{tid}-copy{i + 1}'
            act = {'kind': 'reassign-id', 'file': c['file'], 'id': tid,
                   'new_id': new_id, 'heading': c['heading'][:70],
                   'canonical': canonical['file']}
            if (c['state'] in CLOSABLE and c['norm'] == canonical['norm']
                    and canonical['state'] in OPEN_STATES):
                act['also_close'] = True
            actions.append(act)
    return actions


def plan_digests(tasks):
    actions = []
    ns = [t for t in tasks
          if not t['archive'] and t['state'] in CLOSABLE and t['id']
          and str(t['id']).startswith('org-ns-digest-')]
    if len(ns) > 1:
        ns.sort(key=lambda t: t['id'])
        for t in ns[:-1]:
            actions.append({'kind': 'close', 'file': t['file'], 'id': t['id'],
                            'state': 'CANCELLED',
                            'reason': f'superseded by {ns[-1]["id"]} — digests expire daily ({STAMP})',
                            'heading': t['heading'][:70]})
    for t in tasks:
        if (not t['archive'] and t['state'] in CLOSABLE and t['id']
                and t['heading'].startswith('Daily News Digest')):
            actions.append({'kind': 'close', 'file': t['file'], 'id': t['id'],
                            'state': 'CANCELLED',
                            'reason': f'expired perishable digest — task-audit cleanup {STAMP}',
                            'heading': t['heading'][:70]})
    return actions


def apply_actions(actions):
    by_file = defaultdict(list)
    for a in actions:
        by_file[a['file']].append(a)
    done, errors = 0, []
    for f, acts in sorted(by_file.items()):
        try:
            ws = OrgWorkspace()
            ws.load(f)
        except Exception as e:
            errors.append((f, str(e)[:80]))
            continue
        touched = False
        for a in acts:
            node = ws.find_by_id(a['id'])
            if node is None:
                errors.append((f, f"id not found: {a['id']}"))
                continue
            try:
                if a['kind'] == 'close':
                    ws.transition(node, a['state'])
                    ws.set_property(node, 'CLOSED_REASON', a['reason'])
                elif a['kind'] == 'reassign-id':
                    ws.set_property(node, 'ID', a['new_id'])
                    ws.set_property(node, 'ID_REASSIGNED',
                                    f"was {a['id']} (dup of {a['canonical']}) {STAMP}")
                    if a.get('also_close'):
                        ws.transition(node, 'CANCELLED')
                        ws.set_property(node, 'CLOSED_REASON',
                                        f"routing copy of {a['canonical']} — task-audit cleanup {STAMP}")
                touched = True
                done += 1
            except Exception as e:
                errors.append((f, f"{a['id']}: {str(e)[:80]}"))
        if touched:
            ws.save_all()
    return done, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('bucket', nargs='?', default='all',
                    choices=['zombies', 'dup-ids', 'digests', 'all'])
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    tasks = scan()
    actions = []
    if args.bucket in ('zombies', 'all'):
        actions += plan_zombies(tasks)
    if args.bucket in ('dup-ids', 'all'):
        actions += plan_dup_ids(tasks)
    if args.bucket in ('digests', 'all'):
        actions += plan_digests(tasks)

    # dedupe: a task may appear in zombies AND digests — first action wins
    seen, unique = set(), []
    for a in actions:
        key = (a['file'], a['id'], a['kind'])
        if key not in seen:
            seen.add(key)
            unique.append(a)

    for a in unique:
        tag = a['kind'] + ('+close' if a.get('also_close') else '')
        target = a.get('state', a.get('new_id', ''))
        print(f"{tag:16} {a['file']:45} {a['id'][:44]:44} -> {target:10} | {a['heading']}")
    print(f"\n{len(unique)} actions "
          f"({sum(1 for a in unique if a['kind'] == 'close')} close, "
          f"{sum(1 for a in unique if a['kind'] == 'reassign-id')} reassign-id)")

    if not args.apply:
        print('dry run — re-run with --apply')
        return
    done, errors = apply_actions(unique)
    print(f'applied: {done}, errors: {len(errors)}')
    for f, e in errors[:15]:
        print(f'  ERR {f}: {e}')


if __name__ == '__main__':
    main()
