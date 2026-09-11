#!/usr/bin/env python3
"""
Write-Back Engine (DIP-0004)

Enables bidirectional sync by writing DB changes back to source files.
Maintains file-as-source-of-truth while allowing programmatic updates.

Key Concepts:
- pending_writes table queues changes
- Conflict detection via checksum comparison
- Atomic writes with backup
- Supports org-mode task updates and note modifications

Schema (from zettel_db.py):
    pending_writes (
        id, table_name, record_id, operation, changes,
        target_file, status, error_message, created_at, applied_at
    )

Usage:
    python writeback_engine.py --process           # Process all pending writes
    python writeback_engine.py --status            # Show pending writes status
    python writeback_engine.py --queue TASK_ID     # Queue a task update
    python writeback_engine.py --clear-failed      # Clear failed writes
"""

import json
import re
import sys
import hashlib
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple

# Add lib to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from zettel_db import get_connection, SPACES, DATA_ROOT


def compute_checksum(content: str) -> str:
    """Compute MD5 checksum of content."""
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def get_file_checksum(file_path: Path) -> str:
    """Get current checksum of a file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        return compute_checksum(f.read())


def get_stored_checksum(file_path: str, space: str = None) -> Optional[str]:
    """Get stored checksum from database."""
    conn = get_connection(space)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT checksum FROM file_checksums WHERE path = ?",
        (file_path,)
    )
    row = cursor.fetchone()
    conn.close()
    return row['checksum'] if row else None


def detect_conflict(file_path: Path, space: str = None) -> bool:
    """Check if file has been modified since last index.

    Returns True if there's a conflict (file changed externally).
    """
    stored = get_stored_checksum(str(file_path), space)
    if stored is None:
        return False  # No stored checksum, no conflict

    current = get_file_checksum(file_path)
    return stored != current


def queue_write(space, table_name, record_id, target_file, operation, changes=None):
    from writeback_store import queue
    return queue(space, table_name, record_id, target_file, operation, changes)


def update_org_task_state(file_path, heading_text, old_state, new_state):
    from writeback_store import update_file
    return update_file(file_path, 'update_state', {'heading': heading_text,
        'old_state': old_state, 'new_state': new_state})


def update_org_task_property(file_path, heading_text, property_name, new_value):
    from writeback_store import update_file
    return update_file(file_path, 'update_property', {'heading': heading_text,
        'property': property_name, 'new_value': new_value})


def process_pending_write(write_id, space):
    from writeback_store import process
    return process(write_id, space)


def process_all_pending(space: str = None) -> Dict[str, int]:
    """Process all pending writes.

    Returns dict with counts.
    """
    stats = {
        'processed': 0,
        'succeeded': 0,
        'failed': 0,
        'conflicts': 0,
    }

    spaces_to_process = [space] if space else list(SPACES.keys())

    for sp in spaces_to_process:
        conn = get_connection(sp)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id FROM pending_writes WHERE status = 'pending'
            ORDER BY created_at
        """)

        pending_ids = [row['id'] for row in cursor.fetchall()]
        conn.close()

        for write_id in pending_ids:
            success, message = process_pending_write(write_id, sp)
            stats['processed'] += 1

            if success:
                stats['succeeded'] += 1
                print(f"  [OK] Write {write_id}: {message}")
            elif 'conflict' in message.lower():
                stats['conflicts'] += 1
                print(f"  [CONFLICT] Write {write_id}: {message}")
            else:
                stats['failed'] += 1
                print(f"  [FAIL] Write {write_id}: {message}")

    return stats


def get_pending_status(space: str = None) -> Dict[str, Any]:
    """Get status of pending writes."""
    status = {
        'pending': 0,
        'completed': 0,
        'failed': 0,
        'conflicts': 0,
        'recent_failed': [],
    }

    spaces_to_check = [space] if space else list(SPACES.keys())

    for sp in spaces_to_check:
        conn = get_connection(sp)
        cursor = conn.cursor()

        # Count by status
        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM pending_writes
            GROUP BY status
        """)

        for row in cursor.fetchall():
            if row['status'] == 'pending':
                status['pending'] += row['count']
            elif row['status'] == 'completed':
                status['completed'] += row['count']
            elif row['status'] == 'failed':
                status['failed'] += row['count']
            elif row['status'] == 'conflict':
                status['conflicts'] += row['count']

        # Get recent failures
        cursor.execute("""
            SELECT id, target_file, operation, error_message, created_at
            FROM pending_writes
            WHERE status IN ('failed', 'conflict')
            ORDER BY created_at DESC
            LIMIT 5
        """)

        for row in cursor.fetchall():
            status['recent_failed'].append({
                'id': row['id'],
                'path': row['target_file'],
                'type': row['operation'],
                'error': row['error_message'],
                'space': sp,
            })

        conn.close()

    return status


def clear_failed_writes(space: str = None) -> int:
    """Clear failed writes from queue.

    Returns count of cleared writes.
    """
    count = 0
    spaces_to_clear = [space] if space else list(SPACES.keys())

    for sp in spaces_to_clear:
        conn = get_connection(sp)
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM pending_writes
            WHERE status IN ('failed', 'conflict')
        """)

        count += cursor.rowcount
        conn.commit()
        conn.close()

    return count


def queue_task_completion(task_id: int, space: str) -> int:
    """Convenience function to queue marking a task as DONE.

    Returns write ID.
    """
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT heading, state, source_file FROM tasks WHERE id = ?
    """, (task_id,))

    task = cursor.fetchone()
    conn.close()

    if not task:
        raise ValueError(f"Task {task_id} not found")

    return queue_write(
        space=space,
        table_name='tasks',
        record_id=task_id,
        target_file=task['source_file'],
        operation='update_state',
        changes={
            'heading': task['heading'],
            'old_state': task['state'],
            'new_state': 'DONE'
        }
    )


def print_status(status: Dict[str, Any]):
    """Print write-back status."""
    print("\n" + "=" * 50)
    print("WRITE-BACK STATUS")
    print("=" * 50)
    print(f"Pending:    {status['pending']}")
    print(f"Completed:  {status['completed']}")
    print(f"Failed:     {status['failed']}")
    print(f"Conflicts:  {status['conflicts']}")

    if status['recent_failed']:
        print("\nRecent Failures:")
        for fail in status['recent_failed']:
            print(f"  [{fail['space']}] {fail['type']}: {fail['error']}")
            print(f"       Path: {fail['path']}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Write-Back Engine")
    parser.add_argument('--process', action='store_true', help='Process all pending writes')
    parser.add_argument('--status', action='store_true', help='Show pending writes status')
    parser.add_argument('--clear-failed', action='store_true', help='Clear failed writes')
    parser.add_argument('--queue', type=int, metavar='TASK_ID', help='Queue task completion')
    parser.add_argument('--space', '-s', choices=list(SPACES.keys()), help='Space to operate on')

    args = parser.parse_args()

    if args.process:
        print("\nProcessing pending writes...")
        stats = process_all_pending(args.space)
        print(f"\nResults: {stats['succeeded']} succeeded, {stats['failed']} failed, {stats['conflicts']} conflicts")

    elif args.status:
        status = get_pending_status(args.space)
        print_status(status)

    elif args.clear_failed:
        count = clear_failed_writes(args.space)
        print(f"Cleared {count} failed writes")

    elif args.queue:
        if not args.space:
            print("--space required when queueing")
            sys.exit(1)
        try:
            write_id = queue_task_completion(args.queue, args.space)
            print(f"Queued write {write_id} for task {args.queue}")
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    else:
        parser.print_help()
