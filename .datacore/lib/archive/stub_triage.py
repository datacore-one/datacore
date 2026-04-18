#!/usr/bin/env python3
"""
ARCHIVED 2026-04-18 — Stubs removed from Datacore.

Stubs were auto-generated empty .md files for every unresolved [[wikilink]].
14,389 stubs were deleted; unresolved links are now tracked via resolved=0
in the knowledge.db links table. See session journal 2026-04-18 for details.

Original description:
Stub Triage — Classify stubs into delete/keep/expand categories.

Usage:
    python stub_triage.py                    # Dry-run: output counts and lists
    python stub_triage.py --execute          # Move delete-category stubs to archive
    python stub_triage.py --output-dir DIR   # Write lists to DIR (default: stdout)
"""
import sys
import os
import shutil
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from zettel_db import get_db_path, DATA_ROOT

META_STUBS = {'stub', 'needs-content', 'chatgpt-export', 'Telegram Export December 2025'}
ARCHIVE_DIR = DATA_ROOT / '.datacore' / 'state' / 'stub-archive'
EXPAND_THRESHOLD = 3  # minimum real incoming links to qualify for expansion


def get_stubs_with_real_links(db_path):
    """Query all stubs with their real incoming link count (excluding meta-stub sources)."""
    conn = sqlite3.connect(str(db_path))

    # Get meta-stub file IDs to exclude from link counting
    meta_ids = set()
    for title in META_STUBS:
        row = conn.execute("SELECT id FROM files WHERE title = ? AND is_stub = 1", (title,)).fetchone()
        if row:
            meta_ids.add(row[0])

    if not meta_ids:
        meta_ids = {'__none__'}
    placeholders = ','.join('?' for _ in meta_ids)
    meta_id_params = list(meta_ids)

    query = f"""
    SELECT f.id, f.title, f.path,
        (SELECT COUNT(*) FROM links l
         WHERE l.target_id = f.id
         AND l.source_id NOT IN ({placeholders})
        ) as real_incoming
    FROM files f
    WHERE f.is_stub = 1
    """
    rows = conn.execute(query, meta_id_params).fetchall()
    conn.close()
    return rows  # [(id, title, path, real_incoming), ...]


def classify(stubs):
    """Classify stubs into delete/keep/expand."""
    delete, keep, expand = [], [], []
    for stub_id, title, path, real_incoming in stubs:
        if title in META_STUBS:
            delete.append((stub_id, title, path, real_incoming, 'meta-stub'))
        elif real_incoming == 0:
            delete.append((stub_id, title, path, real_incoming, 'zero-links'))
        elif real_incoming < EXPAND_THRESHOLD:
            keep.append((stub_id, title, path, real_incoming))
        else:
            expand.append((stub_id, title, path, real_incoming))

    # Sort expand by incoming desc (highest value first)
    expand.sort(key=lambda x: x[3], reverse=True)
    return delete, keep, expand


def execute_delete(delete_list, dry_run=True):
    """Move files to archive directory. Returns count of moved files."""
    if dry_run:
        return len(delete_list)

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    moved = 0
    log_path = ARCHIVE_DIR / f"triage-log-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"

    with open(log_path, 'w') as log:
        for stub_id, title, path, real_incoming, reason in delete_list:
            src = Path(path)
            if not src.exists():
                log.write(f"SKIP (missing): {path} | reason={reason}\n")
                continue
            dst = ARCHIVE_DIR / src.name
            # Handle name collisions
            if dst.exists():
                dst = ARCHIVE_DIR / f"{src.stem}_{stub_id}{src.suffix}"
            shutil.move(str(src), str(dst))
            log.write(f"MOVED: {path} -> {dst} | reason={reason} | links={real_incoming}\n")
            moved += 1

    print(f"Log written to: {log_path}")
    return moved


def write_lists(delete, keep, expand, output_dir=None):
    """Write classification lists to files or stdout."""
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        with open(out / 'delete.txt', 'w') as f:
            for _, title, path, links, reason in delete:
                f.write(f"{path}\t{title}\t{links}\t{reason}\n")
        with open(out / 'keep.txt', 'w') as f:
            for _, title, path, links in keep:
                f.write(f"{path}\t{title}\t{links}\n")
        with open(out / 'expand.txt', 'w') as f:
            for _, title, path, links in expand:
                f.write(f"{path}\t{title}\t{links}\n")
        print(f"Lists written to {output_dir}/")
    else:
        print(f"\n{'='*60}")
        print(f"STUB TRIAGE RESULTS")
        print(f"{'='*60}")
        print(f"  DELETE:  {len(delete):,} stubs (meta-stubs + zero incoming)")
        print(f"  KEEP:    {len(keep):,} stubs (1-2 incoming)")
        print(f"  EXPAND:  {len(expand):,} stubs (3+ incoming)")
        print(f"  TOTAL:   {len(delete)+len(keep)+len(expand):,} stubs")
        print(f"\nTop 20 expand candidates:")
        for _, title, _, links in expand[:20]:
            print(f"  {links:4d} incoming  {title}")


def main():
    parser = argparse.ArgumentParser(description='Stub Triage')
    parser.add_argument('--execute', action='store_true', help='Move delete-category stubs to archive')
    parser.add_argument('--output-dir', help='Write lists to directory')
    parser.add_argument('--space', default='personal', help='Space to triage (default: personal)')
    args = parser.parse_args()

    db_path = get_db_path(args.space)
    print(f"Reading from: {db_path}")

    stubs = get_stubs_with_real_links(db_path)
    delete, keep, expand = classify(stubs)
    write_lists(delete, keep, expand, args.output_dir)

    if args.execute:
        print(f"\nExecuting: moving {len(delete)} files to {ARCHIVE_DIR}")
        moved = execute_delete(delete, dry_run=False)
        print(f"Moved {moved} files.")
    elif not args.output_dir:
        print(f"\nDry run. Use --execute to move delete-category stubs.")


if __name__ == '__main__':
    main()
