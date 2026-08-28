#!/usr/bin/env python3
"""task_audit.py — whole-system task analysis: duplicates and inefficiencies.

Loads every [0-9]-*/org/*.org via org_workspace (one workspace per file, so
dedup_ids never crosses file boundaries) and reports:

  1. counts by space x state, live vs archive
  2. near-duplicate clusters among OPEN tasks (within and across spaces)
  3. zombie twins — open tasks whose close-match is already DONE/CANCELLED
     anywhere (the Fundneider class, 2026-08-28)
  4. shared external identity — same :EXTERNAL_ID:, GitHub ref, or URL in
     more than one open task
  5. person-name twins — the same First-Last name in open tasks across spaces
  6. staleness — long-past scheduled/deadline, ancient WAITING, old inbox
  7. monitor-style tasks that should probably be cadences
  8. duplicate :ID: values appearing in more than one file

Usage:
    python3 .datacore/lib/task_audit.py [--report PATH] [--json PATH]

Read-only. Writes nothing except the report file(s) you ask for.
"""
import argparse
import glob
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from org_workspace import OrgWorkspace  # noqa: E402

# DIP-0009 v2.0 canon: seven states. DEFERRED is CLOSED ("done deciding,
# not now"). Retired keywords (QUEUED/WORKING/FAILED/PROJECT/ACTIVE/ASSIGN/
# PAUSED) are kept in OPEN transitional so pre-migration files still count
# as open rather than vanishing — the lint flags them for migration.
OPEN_STATES = {'TODO', 'NEXT', 'WAITING', 'REVIEW',
               'QUEUED', 'WORKING', 'FAILED', 'PROJECT', 'ACTIVE', 'ASSIGN',
               'PAUSED'}
CLOSED_STATES = {'DONE', 'COMPLETED', 'CANCELLED', 'DEFERRED'}
STOPWORDS = {'the', 'a', 'an', 'to', 'of', 'in', 'on', 'for', 'and', 'or',
             'with', 'from', 'into', 'via', 'per', 'after', 'before', 'when'}
DATE_RE = re.compile(r'(\d{4})-(\d{2})-(\d{2})')
GH_REF_RE = re.compile(r'([\w.-]+/[\w.-]+)?#(\d{2,5})\b')
URL_RE = re.compile(r'https?://\S+')
NAME_RE = re.compile(r'\b([A-Z][a-zÀ-ž]+)\s+([A-Z][a-zÀ-žč]+)\b')
MONITOR_RE = re.compile(
    r'^(monitor|check|verify|re-?check|revisit|follow[- ]?up|watch)\b', re.I)
NAME_BLOCKLIST = {'Show Hn', 'Pull Request', 'Api Key', 'Claude Code',
                  'Data Escrow', 'Chief Staff'}


def parse_date(val):
    if val is None:
        return None
    try:
        # vendored orgparse OrgDate raises TypeError in __repr__/__str__
        # when the date is empty — treat as absent
        s = str(val)
    except TypeError:
        return None
    m = DATE_RE.search(s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def normalize(heading):
    h = re.sub(r'\[#[A-C]\]', ' ', str(heading or ''))
    h = re.sub(r':[\w:@]+:\s*$', ' ', h)
    h = re.sub(r'[<\[]\d{4}-\d{2}-\d{2}[^>\]]*[>\]]', ' ', h)
    h = re.sub(r'[^\w\s#/.-]', ' ', h.lower())
    return re.sub(r'\s+', ' ', h).strip()


def tokens_of(norm):
    return {t for t in norm.split() if len(t) >= 4 and t not in STOPWORDS}


def load_tasks():
    tasks, failures = [], []
    for f in sorted(glob.glob('[0-9]-*/org/*.org')):
        space = f.split('/')[0]
        is_archive = 'archive' in Path(f).name
        try:
            ws = OrgWorkspace()
            ws.load(f)
            nodes = list(ws.all_nodes())
        except Exception as e:
            failures.append((f, str(e)[:80]))
            continue
        for n in nodes:
            state = getattr(n, 'todo', None)
            if not state:
                continue
            props = {}
            try:
                props = dict(n.properties or {})
            except Exception:
                pass
            heading = str(getattr(n, 'heading', '') or '')
            body = ''
            try:
                body = str(n.body or '')[:400]
            except Exception:
                pass
            norm = normalize(heading)
            tasks.append({
                'file': f, 'space': space, 'archive': is_archive,
                'state': state, 'heading': heading, 'norm': norm,
                'tokens': tokens_of(norm),
                'id': props.get('ID') or (n.id() if callable(getattr(n, 'id', None)) else None),
                'created': parse_date(props.get('CREATED')),
                'scheduled': parse_date(getattr(n, 'scheduled', None)),
                'deadline': parse_date(getattr(n, 'deadline', None)),
                'closed': parse_date(getattr(n, 'closed', None)),
                'external_id': props.get('EXTERNAL_ID'),
                'tags': list(getattr(n, 'tags', []) or []),
                'refs': sorted({f"{r[0] or '?'}#{r[1]}"
                                for r in GH_REF_RE.findall(heading + ' ' + body)}),
                'urls': sorted(set(URL_RE.findall(heading + ' ' + body)))[:3],
            })
    return tasks, failures


def similarity(a, b):
    return SequenceMatcher(None, a['norm'], b['norm']).ratio()


def candidate_pairs(tasks, max_df=60):
    index = defaultdict(list)
    for i, t in enumerate(tasks):
        for tok in t['tokens']:
            index[tok].append(i)
    pairs = set()
    for tok, idxs in index.items():
        if len(idxs) > max_df:
            continue
        for x in range(len(idxs)):
            for y in range(x + 1, len(idxs)):
                a, b = idxs[x], idxs[y]
                if len(tasks[a]['tokens'] & tasks[b]['tokens']) >= 2:
                    pairs.add((a, b))
    return pairs


def cluster(pairs, n):
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    return [g for g in groups.values() if len(g) > 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--report', default=None, help='markdown report path')
    ap.add_argument('--json', dest='json_path', default=None)
    args = ap.parse_args()

    today = date.today()
    tasks, failures = load_tasks()
    live = [t for t in tasks if not t['archive']]
    open_live = [t for t in live if t['state'] in OPEN_STATES]
    closed_all = [t for t in tasks if t['state'] in CLOSED_STATES]

    by_space_state = defaultdict(Counter)
    for t in live:
        by_space_state[t['space']][t['state']] += 1

    # --- near-duplicate clusters among open tasks ---
    pairs = candidate_pairs(open_live)
    dup_pairs = [(a, b) for a, b in pairs
                 if similarity(open_live[a], open_live[b]) >= 0.82
                 or (len(open_live[a]['tokens'] & open_live[b]['tokens'])
                     / max(1, len(open_live[a]['tokens'] | open_live[b]['tokens']))) >= 0.6]
    clusters = cluster(dup_pairs, len(open_live))
    dup_clusters = []
    for g in sorted(clusters, key=len, reverse=True):
        members = [open_live[i] for i in g]
        spaces = sorted({m['space'] for m in members})
        dup_clusters.append({'cross_space': len(spaces) > 1, 'spaces': spaces,
                             'members': members})

    # --- zombie twins: open task closely matching a CLOSED task anywhere ---
    zombies = []
    closed_index = defaultdict(list)
    for i, t in enumerate(closed_all):
        for tok in t['tokens']:
            closed_index[tok].append(i)
    for t in open_live:
        cands = Counter()
        for tok in t['tokens']:
            if len(closed_index[tok]) <= 60:
                for i in closed_index[tok]:
                    cands[i] += 1
        best, best_sim = None, 0.0
        for i, shared in cands.items():
            if shared < 2:
                continue
            s = similarity(t, closed_all[i])
            if s > best_sim:
                best, best_sim = closed_all[i], s
        if best is not None and best_sim >= 0.85:
            zombies.append({'open': t, 'closed': best, 'sim': round(best_sim, 2)})

    # --- shared external identity ---
    ext_groups = defaultdict(list)
    for t in open_live:
        if t['external_id']:
            ext_groups[('EXTERNAL_ID', t['external_id'])].append(t)
        for r in t['refs']:
            ext_groups[('ref', r)].append(t)
        for u in t['urls']:
            ext_groups[('url', u)].append(t)
    ext_dups = {k: v for k, v in ext_groups.items() if len(v) > 1}

    # --- person-name twins across spaces ---
    name_groups = defaultdict(list)
    for t in open_live:
        for m in NAME_RE.finditer(t['heading']):
            name = f'{m.group(1)} {m.group(2)}'
            if name not in NAME_BLOCKLIST:
                name_groups[name].append(t)
    name_dups = {n: v for n, v in name_groups.items()
                 if len(v) > 1 and len({t['space'] for t in v}) > 1}

    # --- staleness ---
    def days(d):
        return (today - d).days if d else None
    overdue = [t for t in open_live
               if (t['deadline'] and days(t['deadline']) > 0)
               or (t['scheduled'] and days(t['scheduled']) > 45)]
    overdue.sort(key=lambda t: -(days(t['deadline']) or days(t['scheduled']) or 0))
    old_waiting = [t for t in open_live if t['state'] == 'WAITING'
                   and t['created'] and days(t['created']) > 60]
    ancient = [t for t in open_live if t['created'] and days(t['created']) > 180]
    inbox_stats = {}
    for t in live:
        if Path(t['file']).name == 'inbox.org' and t['state'] in OPEN_STATES:
            s = inbox_stats.setdefault(t['space'], {'count': 0, 'oldest': None})
            s['count'] += 1
            if t['created'] and (s['oldest'] is None or t['created'] < s['oldest']):
                s['oldest'] = t['created']

    monitors = [t for t in open_live if MONITOR_RE.match(t['heading'])]

    id_files = defaultdict(set)
    for t in tasks:
        if t['id']:
            id_files[t['id']].add(t['file'])
    dup_ids = {i: sorted(fs) for i, fs in id_files.items() if len(fs) > 1}

    ai_open = [t for t in open_live if 'AI' in t['tags']]

    # ---------- report ----------
    def fmt(t, sim=None):
        d = t['scheduled'] or t['deadline']
        extra = f' sched {d}' if d else ''
        s = f' ~{sim}' if sim else ''
        return (f"- `{t['state']}` [{t['space']}] {t['heading'][:95]}"
                f" ({t['id'] or 'no-id'}{extra}){s}")

    L = [f'# Task Audit — {today}', '',
         f"Scanned {len(tasks)} tasks in {len(set(t['file'] for t in tasks))} files "
         f"({len(live)} live, {len(tasks)-len(live)} archived). "
         f"Open live tasks: **{len(open_live)}**.", '']
    if failures:
        L += ['## Files that failed to parse', ''] + \
             [f'- {f}: {e}' for f, e in failures] + ['']

    L += ['## Counts by space (live files)', '',
          '| space | ' + ' | '.join(sorted(OPEN_STATES | CLOSED_STATES)) + ' |',
          '|---|' + '---|' * len(OPEN_STATES | CLOSED_STATES)]
    for sp in sorted(by_space_state):
        row = [str(by_space_state[sp].get(s, '')) for s in sorted(OPEN_STATES | CLOSED_STATES)]
        L.append(f'| {sp} | ' + ' | '.join(row) + ' |')
    L.append('')

    xs = [c for c in dup_clusters if c['cross_space']]
    ws_ = [c for c in dup_clusters if not c['cross_space']]
    L += [f'## Near-duplicate clusters among open tasks — {len(dup_clusters)} '
          f'({len(xs)} cross-space, {len(ws_)} within-space)', '']
    for c in dup_clusters[:40]:
        L.append(f"**cluster** ({'CROSS-SPACE ' if c['cross_space'] else ''}"
                 f"{'+'.join(c['spaces'])}):")
        L += [fmt(m) for m in c['members'][:6]]
        L.append('')

    L += [f'## Zombie twins — {len(zombies)} open tasks whose close-match is already closed', '']
    for z in sorted(zombies, key=lambda z: -z['sim'])[:40]:
        L.append(fmt(z['open'], z['sim']))
        L.append(f"  ↳ closed twin: `{z['closed']['state']}` [{z['closed']['space']}] "
                 f"{z['closed']['heading'][:80]} ({z['closed']['file']})")
    L.append('')

    L += [f'## Shared external identity — {len(ext_dups)} groups', '']
    for (kind, key), members in list(ext_dups.items())[:25]:
        L.append(f'**{kind}: {key}**')
        L += [fmt(m) for m in members[:5]]
        L.append('')

    L += [f'## Person-name twins across spaces — {len(name_dups)} names', '']
    for name, members in sorted(name_dups.items())[:25]:
        L.append(f'**{name}**')
        L += [fmt(m) for m in members[:5]]
        L.append('')

    L += [f'## Staleness', '',
          f'- Overdue / long-past schedule: **{len(overdue)}**',
          f'- WAITING older than 60 days: **{len(old_waiting)}**',
          f'- Open tasks created >180 days ago: **{len(ancient)}**',
          f"- Open `:AI:` tasks: **{len(ai_open)}**", '',
          '### Worst overdue (top 25)', '']
    L += [fmt(t) for t in overdue[:25]]
    L += ['', '### Inbox hygiene (inbox.org is meant to be empty)', '']
    for sp, s in sorted(inbox_stats.items()):
        L.append(f"- {sp}: **{s['count']}** open items, oldest {s['oldest']}")

    L += ['', f'## Monitor-style tasks (cadence candidates) — {len(monitors)}', '']
    L += [fmt(t) for t in monitors[:25]]

    L += ['', f'## Duplicate :ID: values in multiple files — {len(dup_ids)}', '']
    for i, fs in list(dup_ids.items())[:20]:
        L.append(f'- `{i}` → {", ".join(fs)}')

    report = '\n'.join(L) + '\n'
    if args.report:
        Path(args.report).write_text(report, encoding='utf-8')
    if args.json_path:
        Path(args.json_path).write_text(json.dumps({
            'date': str(today), 'total': len(tasks), 'open_live': len(open_live),
            'dup_clusters': len(dup_clusters), 'cross_space_clusters': len(xs),
            'zombies': len(zombies), 'ext_dups': len(ext_dups),
            'name_dups': len(name_dups), 'overdue': len(overdue),
            'old_waiting': len(old_waiting), 'ancient': len(ancient),
            'monitors': len(monitors), 'dup_ids': len(dup_ids),
            'ai_open': len(ai_open),
            'inbox': {k: {'count': v['count'], 'oldest': str(v['oldest'])}
                      for k, v in inbox_stats.items()},
        }, indent=2), encoding='utf-8')

    print(f"tasks={len(tasks)} live_open={len(open_live)} | "
          f"dup_clusters={len(dup_clusters)} (cross-space {len(xs)}) | "
          f"zombies={len(zombies)} | ext_id_groups={len(ext_dups)} | "
          f"name_twins={len(name_dups)} | overdue={len(overdue)} | "
          f"waiting60d={len(old_waiting)} | ancient180d={len(ancient)} | "
          f"monitors={len(monitors)} | dup_ids={len(dup_ids)} | ai_open={len(ai_open)}")


if __name__ == '__main__':
    main()
