#!/usr/bin/env python3
"""Queue AI-classified tasks into the nightshift pipeline.

Reads a triage JSON (produced by an AI-classification pass over a task
dump) and, for every task classed `ai` or `ai_prep`:

  1. moves it from the space's next_actions.org to the global
     nightshift.org (0-personal/org/ — the deliberation AI queue,
     DIP-0009 Part 0),
  2. enriches it with Rich Task Standard properties (CONTEXT,
     ACCEPTANCE_CRITERIA, EFFORT — the executor rejects tasks without
     them, see DIP-0031 'specification' category) plus AI_CLASS/VALUE,
  3. retags it with the assigned :AI:<type>: dispatch tag (the
     nightshift scanner selects on AI-prefixed tags in TODO/NEXT).

ai_prep tasks get an explicit DRAFT-ONLY guardrail appended to CONTEXT —
the executor must produce a ready-to-send draft, never send/publish.

Usage:
  python3 .datacore/lib/queue_ai_tasks.py TRIAGE.json SOURCE.org [--dry-run]

First used 2026-06-11 to queue 113 of 170 0-personal tasks.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2]
ADAPTER = DATA_DIR / '.datacore' / 'lib' / 'org_workspace_adapter.py'
TARGET = DATA_DIR / '0-personal' / 'org' / 'nightshift.org'

DRAFT_GUARD = (' [DRAFT-ONLY: produce a ready-to-send draft for human '
               'review. Do NOT send, publish, or post anything.]')


def _adapter(args, timeout=120):
    return subprocess.run([sys.executable, str(ADAPTER), *args],
                          cwd=DATA_DIR, capture_output=True, text=True,
                          timeout=timeout)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('triage', help='Triage JSON path')
    p.add_argument('source', help='Source org file (next_actions.org)')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    triage = json.loads(Path(args.triage).read_text(encoding='utf-8'))
    queue = [t for t in triage['tasks'] if t.get('class') in ('ai', 'ai_prep')]
    queue.sort(key=lambda t: -(t.get('value') or 0))

    moved, failed = 0, []
    for t in queue:
        tid, heading = t['id'], t.get('heading', '?')
        if args.dry_run:
            print(f"DRY: would queue [{t['class']}/v{t.get('value')}] {heading[:80]}")
            continue
        mv = _adapter(['move', '--from', args.source, '--to', str(TARGET),
                       '--id', tid])
        if mv.returncode != 0:
            failed.append((tid, heading, (mv.stderr or mv.stdout).strip()[-160:]))
            continue
        context = t.get('context', '')
        if t['class'] == 'ai_prep':
            context += DRAFT_GUARD
        ai_tag = (t.get('ai_tag') or ':AI:').strip(':')  # e.g. AI:research
        up = _adapter(['update', '--file', str(TARGET), '--id', tid,
                       '--tags', f":{ai_tag}:",
                       '--property', f"CONTEXT={context}",
                       '--property', f"ACCEPTANCE_CRITERIA={t.get('acceptance_criteria', '')}",
                       '--property', f"EFFORT={t.get('effort', '1h')}",
                       '--property', f"AI_CLASS={t['class']}",
                       '--property', f"VALUE={t.get('value', '')}"])
        if up.returncode != 0:
            failed.append((tid, heading, 'moved but enrich failed: '
                           + (up.stderr or up.stdout).strip()[-160:]))
            continue
        moved += 1
        print(f"queued [{t['class']}/v{t.get('value')}] {heading[:80]}")

    print(f"\n[queue-ai] queued: {moved}/{len(queue)}, failed: {len(failed)}")
    for tid, heading, err in failed:
        print(f"  FAILED {tid}: {heading[:60]} — {err}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
