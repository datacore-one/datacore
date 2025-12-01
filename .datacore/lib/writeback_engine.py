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


def queue_write(
    space: str,
    table_name: str,
    record_id: int,
    target_file: str,
    operation: str,
    changes: Dict[str, Any] = None
) -> int:
    """Queue a write operation for later processing.

    Args:
        space: Which space database to use
        table_name: Source table (tasks, files, etc.)
        record_id: ID of the record being updated
        target_file: File path to write to
        operation: Type of change (update_state, update_property, append)
        changes: Dict with change details (old_value, new_value, etc.)

    Returns:
        ID of the queued write
    """
    conn = get_connection(space)
    cursor = conn.cursor()

    changes_json = json.dumps(changes) if changes else None

    cursor.execute("""
        INSERT INTO pending_writes
        (table_name, record_id, operation, changes, target_file, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
    """, (
        table_name,
        record_id,
        operation,
        changes_json,
        target_file
    ))

    write_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return write_id


def update_org_task_state(
    file_path: Path,
    heading_text: str,
    old_state: str,
    new_state: str
) -> Tuple[bool, str]:
    """Update a task state in an org file.

    Args:
        file_path: Path to org file
        heading_text: The heading text to find
        old_state: Expected current state (for verification)
        new_state: New state to set

    Returns:
        Tuple of (success, message/error)
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Build pattern to find the task
    # Format: ** TODO/DONE/etc heading_text
    escaped_heading = re.escape(heading_text)
    pattern = rf'^(\*+\s+){old_state}(\s+{escaped_heading})'

    match = re.search(pattern, content, re.MULTILINE)
    if not match:
        # Try without state for new tasks
        pattern_no_state = rf'^(\*+\s+)({escaped_heading})'
        match = re.search(pattern_no_state, content, re.MULTILINE)
        if not match:
            return False, f"Task not found: {heading_text}"

    # Replace the state
    new_content = re.sub(
        pattern,
        rf'\g<1>{new_state}\g<2>',
        content,
        count=1,
        flags=re.MULTILINE
    )

    if new_content == content:
        return False, "No changes made"

    # Write back with backup
    backup_path = file_path.with_suffix('.org.bak')
    shutil.copy2(file_path, backup_path)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    return True, f"Updated: {old_state} -> {new_state}"


def update_org_task_property(
    file_path: Path,
    heading_text: str,
    property_name: str,
    new_value: str
) -> Tuple[bool, str]:
    """Update a property in an org task's property drawer.

    Args:
        file_path: Path to org file
        heading_text: The heading text to find
        property_name: Property to update (e.g., 'UPDATED')
        new_value: New property value

    Returns:
        Tuple of (success, message/error)
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Find the heading
    heading_idx = None
    for i, line in enumerate(lines):
        if heading_text in line and line.strip().startswith('*'):
            heading_idx = i
            break

    if heading_idx is None:
        return False, f"Heading not found: {heading_text}"

    # Find property drawer
    props_start = None
    props_end = None
    for i in range(heading_idx + 1, min(heading_idx + 20, len(lines))):
        line = lines[i].strip()
        if line == ':PROPERTIES:':
            props_start = i
        elif line == ':END:' and props_start is not None:
            props_end = i
            break
        elif line.startswith('*'):  # Next heading, no properties
            break

    if props_start is None:
        return False, "No property drawer found"

    # Find and update property
    prop_pattern = rf'^(\s*:{property_name}:\s*)(.*)$'
    found = False
    for i in range(props_start + 1, props_end):
        match = re.match(prop_pattern, lines[i], re.IGNORECASE)
        if match:
            lines[i] = f"{match.group(1)}{new_value}\n"
            found = True
            break

    if not found:
        # Add property before :END:
        indent = '  '  # Standard org indent
        lines.insert(props_end, f"{indent}:{property_name}: {new_value}\n")

    # Write back with backup
    backup_path = file_path.with_suffix('.org.bak')
    shutil.copy2(file_path, backup_path)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)

    return True, f"Updated property {property_name}"


def process_pending_write(write_id: int, space: str) -> Tuple[bool, str]:
    """Process a single pending write.

    Returns:
        Tuple of (success, message)
    """
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM pending_writes WHERE id = ?
    """, (write_id,))

    write = cursor.fetchone()
    if not write:
        conn.close()
        return False, f"Write {write_id} not found"

    if write['status'] != 'pending':
        conn.close()
        return False, f"Write {write_id} not pending (status: {write['status']})"

    target_path = Path(write['target_file'])
    if not target_path.exists():
        # Mark as failed
        cursor.execute("""
            UPDATE pending_writes
            SET status = 'failed', error_message = ?, applied_at = ?
            WHERE id = ?
        """, (f"File not found: {target_path}", datetime.now().isoformat(), write_id))
        conn.commit()
        conn.close()
        return False, f"File not found: {target_path}"

    # Check for conflict
    if detect_conflict(target_path, space):
        cursor.execute("""
            UPDATE pending_writes
            SET status = 'conflict', error_message = ?, applied_at = ?
            WHERE id = ?
        """, ("File modified since last index", datetime.now().isoformat(), write_id))
        conn.commit()
        conn.close()
        return False, "Conflict: file modified since last index"

    # Parse changes
    changes = json.loads(write['changes']) if write['changes'] else {}

    # Process based on operation type
    success = False
    message = ""

    operation = write['operation']

    if operation == 'update_state':
        # Update org task state
        success, message = update_org_task_state(
            target_path,
            changes.get('heading', ''),
            changes.get('old_state', ''),
            changes.get('new_state', '')
        )

    elif operation == 'update_property':
        success, message = update_org_task_property(
            target_path,
            changes.get('heading', ''),
            changes.get('property', ''),
            changes.get('new_value', '')
        )

    elif operation == 'append':
        # Append content to file
        try:
            with open(target_path, 'a', encoding='utf-8') as f:
                f.write(changes.get('content', ''))
            success = True
            message = "Content appended"
        except Exception as e:
            success = False
            message = str(e)

    else:
        message = f"Unknown operation: {operation}"

    # Update write status
    now = datetime.now().isoformat()
    if success:
        # Update file checksum
        new_checksum = get_file_checksum(target_path)
        cursor.execute("""
            INSERT OR REPLACE INTO file_checksums (path, checksum, indexed_at, modified_at)
            VALUES (?, ?, ?, ?)
        """, (str(target_path), new_checksum, now, now))

        cursor.execute("""
            UPDATE pending_writes
            SET status = 'completed', applied_at = ?
            WHERE id = ?
        """, (now, write_id))
    else:
        cursor.execute("""
            UPDATE pending_writes
            SET status = 'failed', error_message = ?, applied_at = ?
            WHERE id = ?
        """, (message, now, write_id))

    conn.commit()
    conn.close()

    return success, message


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
