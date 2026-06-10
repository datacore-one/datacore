#!/usr/bin/env python3
"""GTD hygiene runner — the maintenance loop the adapter never had.

Runs per space ([0-9]-*/org/next_actions.org):
  - archive-done   (DONE/CANCELLED older than --min-age days; daily)
  - ensure-ids     (add :ID: to tasks missing them; idempotent; daily)
  - deadlines      (overdue report, surfaced in summary; daily)

NOTE: the adapter's `duplicates` command checks ONE title against a file
(pre-add dedup), it is NOT a file-wide duplicate scan — a real scan would
need a new adapter command. Tracked as a follow-up.

Writes a summary JSON to .datacore/state/nightshift/hygiene-{date}.json
(that subdir is git-synced since 2026-06-10) so /today, CoS triage, and
the app can surface results. Prints a human-readable digest to stdout.

Scheduling: invoked by nightshift run.py (Phase 9, nightly on the server).
Manual: python3 .datacore/lib/gtd_hygiene.py [--dry-run] [--min-age N]

Context: as of 2026-06-10, 203 DONE tasks had piled up unarchived and ~80
tasks lacked IDs because these adapter commands existed but nothing ever
invoked them. This script is the wiring, not new capability.
"""

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2]
ADAPTER = DATA_DIR / '.datacore' / 'lib' / 'org_workspace_adapter.py'


def _adapter(args, timeout=180):
    return subprocess.run(
        [sys.executable, str(ADAPTER), *args],
        cwd=DATA_DIR, capture_output=True, text=True, timeout=timeout)


def _parse_json(proc):
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return None


def process_space(org_file: Path, min_age: int, dry_run: bool) -> dict:
    entry = {'file': str(org_file)}

    # 1. Archive old DONE tasks
    #    real run returns {"archived_count": N}; dry-run {"total_candidates": N}
    args = ['archive-done', '--file', str(org_file), '--min-age', str(min_age)]
    if dry_run:
        args.append('--dry-run')
    proc = _adapter(args)
    data = _parse_json(proc)
    if proc.returncode != 0:
        entry['archive_error'] = (proc.stderr or proc.stdout or 'unknown').strip()[-300:]
    elif data is not None:
        entry['archived'] = data.get('archived_count',
                                     data.get('total_candidates', 0))

    # 2. Ensure IDs (skip in dry-run: it writes) — returns {"added_count": N}
    if not dry_run:
        proc = _adapter(['ensure-ids', '--file', str(org_file)])
        data = _parse_json(proc)
        if proc.returncode != 0:
            entry['ensure_ids_error'] = (proc.stderr or 'unknown').strip()[-200:]
        elif data is not None:
            entry['ids_added'] = data.get('added_count', 0)

    # 3. Overdue deadlines (report only) — returns {"overdue": N, ...}
    proc = _adapter(['deadlines', '--file', str(org_file), '--days', '0'])
    data = _parse_json(proc)
    if data is not None:
        entry['overdue_deadlines'] = data.get('overdue', 0)

    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dry-run', action='store_true',
                        help='Report what would be archived; no writes.')
    parser.add_argument('--min-age', type=int, default=7,
                        help='Archive DONE tasks closed more than N days ago.')
    args = parser.parse_args()

    spaces = {}
    for space_dir in sorted(DATA_DIR.glob('[0-9]-*')):
        org_file = space_dir / 'org' / 'next_actions.org'
        if not org_file.exists():
            continue
        try:
            spaces[space_dir.name] = process_space(
                org_file, args.min_age, args.dry_run)
        except Exception as exc:  # noqa: BLE001 — one bad space must not stop the rest
            spaces[space_dir.name] = {
                'file': str(org_file),
                'error': f'{type(exc).__name__}: {exc}',
            }

    summary = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'dry_run': args.dry_run,
        'min_age_days': args.min_age,
        'spaces': spaces,
        'totals': {
            'archived': sum(s.get('archived', 0) for s in spaces.values()),
            'ids_added': sum(s.get('ids_added', 0) for s in spaces.values()),
            'overdue_deadlines': sum(s.get('overdue_deadlines', 0)
                                     for s in spaces.values()),
            'errors': sum(1 for s in spaces.values()
                          if any(k.endswith('error') or k == 'error'
                                 for k in s)),
        },
    }

    if not args.dry_run:
        # maintenance/ subdir: CoS ingest globs *.json at the nightshift
        # state root as execution records — keep non-exec artifacts out
        out_dir = DATA_DIR / '.datacore' / 'state' / 'nightshift' / 'maintenance'
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f'hygiene-{date.today().isoformat()}.json'
        out_path.write_text(json.dumps(summary, indent=2), encoding='utf-8')

    t = summary['totals']
    mode = 'DRY-RUN ' if args.dry_run else ''
    print(f"[gtd-hygiene] {mode}archived={t['archived']} "
          f"ids_added={t['ids_added']} overdue={t['overdue_deadlines']} "
          f"errors={t['errors']}")
    for name, s in spaces.items():
        bits = [f"{k}={v}" for k, v in s.items()
                if k != 'file' and v not in (0, '', None)]
        if bits:
            print(f"  {name}: {' '.join(str(b)[:160] for b in bits)}")

    return 1 if t['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
