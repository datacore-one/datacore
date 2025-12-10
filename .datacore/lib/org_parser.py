#!/usr/bin/env python3
"""
Org-mode Parser (DIP-0004)

Parses org-mode files to extract:
- Tasks (TODO items with properties)
- Projects (PROJECT entries)
- Inbox entries (items in inbox.org)
- Habits (recurring tasks)

Core Principle: Parse org files, index to DB, support write-back.

Usage:
    python org_parser.py <file_path>
    python org_parser.py --scan --space SPACE
    python org_parser.py --sync [--space SPACE]
"""

import re
import sys
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple

# Add lib to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from zettel_db import (
    get_connection, init_database, SPACES, DATA_ROOT, SYSTEM_PATHS
)


# Org-mode regex patterns
TODO_STATES = ['TODO', 'NEXT', 'WAITING', 'DONE', 'CANCELLED', 'PROJECT']
PRIORITY_PATTERN = re.compile(r'\[#([A-C])\]')
TAGS_PATTERN = re.compile(r':([a-zA-Z0-9_@:]+):$')
TIMESTAMP_PATTERN = re.compile(r'[<\[](\d{4}-\d{2}-\d{2})(?: \w{3})?(?: \d{2}:\d{2})?[>\]]')
SCHEDULED_PATTERN = re.compile(r'SCHEDULED:\s*<([^>]+)>')
DEADLINE_PATTERN = re.compile(r'DEADLINE:\s*<([^>]+)>')
CLOSED_PATTERN = re.compile(r'CLOSED:\s*\[([^\]]+)\]')
PROPERTY_PATTERN = re.compile(r':([A-Z_]+):\s*(.+)')


def compute_checksum(content: str) -> str:
    """Compute MD5 checksum of content."""
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def parse_heading(line: str) -> Optional[Dict[str, Any]]:
    """Parse an org-mode heading line.

    Returns dict with: level, state, priority, title, tags
    """
    # Match heading pattern: *+ STATE [#P] Title :tags:
    heading_match = re.match(r'^(\*+)\s+(.*)$', line)
    if not heading_match:
        return None

    level = len(heading_match.group(1))
    rest = heading_match.group(2).strip()

    # Extract state (TODO, DONE, etc.)
    state = None
    for s in TODO_STATES:
        if rest.startswith(s + ' ') or rest == s:
            state = s
            rest = rest[len(s):].strip()
            break

    # Extract priority [#A]
    priority = None
    priority_match = PRIORITY_PATTERN.search(rest)
    if priority_match:
        priority = priority_match.group(1)
        rest = PRIORITY_PATTERN.sub('', rest).strip()

    # Extract tags :tag1:tag2:
    tags = None
    tags_match = TAGS_PATTERN.search(rest)
    if tags_match:
        tags = ':' + tags_match.group(1) + ':'
        rest = TAGS_PATTERN.sub('', rest).strip()

    title = rest.strip()

    return {
        'level': level,
        'state': state,
        'priority': priority,
        'title': title,
        'tags': tags
    }


def parse_properties(lines: List[str], start_idx: int) -> Tuple[Dict[str, str], int]:
    """Parse a :PROPERTIES: drawer.

    Returns (properties_dict, end_index)
    """
    properties = {}
    idx = start_idx

    # Find :PROPERTIES:
    while idx < len(lines):
        line = lines[idx].strip()
        if line == ':PROPERTIES:':
            idx += 1
            break
        elif line and not line.startswith('#'):
            # Non-empty, non-comment line before PROPERTIES
            return properties, start_idx
        idx += 1

    # Parse properties until :END:
    while idx < len(lines):
        line = lines[idx].strip()
        if line == ':END:':
            idx += 1
            break

        prop_match = PROPERTY_PATTERN.match(line)
        if prop_match:
            key = prop_match.group(1)
            value = prop_match.group(2).strip()
            properties[key] = value
        idx += 1

    return properties, idx


def parse_planning(line: str) -> Dict[str, str]:
    """Parse SCHEDULED/DEADLINE/CLOSED line."""
    result = {}

    scheduled_match = SCHEDULED_PATTERN.search(line)
    if scheduled_match:
        result['scheduled'] = scheduled_match.group(1)

    deadline_match = DEADLINE_PATTERN.search(line)
    if deadline_match:
        result['deadline'] = deadline_match.group(1)

    closed_match = CLOSED_PATTERN.search(line)
    if closed_match:
        result['closed'] = closed_match.group(1)

    return result


def parse_org_file(file_path: Path, space: str = None) -> Dict[str, Any]:
    """Parse an org-mode file.

    Returns dict with:
    - tasks: List of task dicts
    - projects: List of project dicts
    - inbox_entries: List of inbox entry dicts (if inbox.org)
    - file_checksum: MD5 of file content
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.split('\n')
    file_checksum = compute_checksum(content)
    file_name = file_path.name.lower()

    tasks = []
    projects = []
    inbox_entries = []

    # Track parent hierarchy
    parent_stack = []  # [(level, task_index)]

    idx = 0
    while idx < len(lines):
        line = lines[idx]

        # Parse heading
        heading = parse_heading(line)
        if heading:
            task = {
                'line_number': idx + 1,
                'level': heading['level'],
                'state': heading['state'],
                'priority': heading['priority'],
                'title': heading['title'],
                'tags': heading['tags'],
                'scheduled': None,
                'deadline': None,
                'closed': None,
                'category': None,
                'effort': None,
                'properties': {},
                'parent_index': None,
                'content': '',
            }

            # Check next line for planning info (SCHEDULED/DEADLINE/CLOSED)
            if idx + 1 < len(lines):
                next_line = lines[idx + 1]
                if 'SCHEDULED:' in next_line or 'DEADLINE:' in next_line or 'CLOSED:' in next_line:
                    planning = parse_planning(next_line)
                    task['scheduled'] = planning.get('scheduled')
                    task['deadline'] = planning.get('deadline')
                    task['closed'] = planning.get('closed')
                    idx += 1

            # Check for properties drawer
            if idx + 1 < len(lines):
                props, new_idx = parse_properties(lines, idx + 1)
                if props:
                    task['properties'] = props
                    task['category'] = props.get('CATEGORY')
                    if 'EFFORT' in props:
                        # Parse effort (e.g., "0:30" -> 30 minutes)
                        effort_str = props['EFFORT']
                        if ':' in effort_str:
                            parts = effort_str.split(':')
                            try:
                                task['effort'] = int(parts[0]) * 60 + int(parts[1])
                            except ValueError:
                                pass
                        else:
                            try:
                                task['effort'] = int(effort_str)
                            except ValueError:
                                pass
                    idx = new_idx - 1

            # Update parent hierarchy
            while parent_stack and parent_stack[-1][0] >= heading['level']:
                parent_stack.pop()

            if parent_stack:
                task['parent_index'] = parent_stack[-1][1]

            # Determine if this is a project or task
            if heading['state'] == 'PROJECT':
                projects.append({
                    'line_number': task['line_number'],
                    'name': task['title'],
                    'status': 'ACTIVE',
                    'category': task['category'],
                    'tags': task['tags'],
                    'properties': task['properties'],
                })
            elif heading['state']:  # Has a TODO state
                tasks.append(task)
                parent_stack.append((heading['level'], len(tasks) - 1))

            # For inbox.org, capture entries under "* Inbox" heading
            if file_name == 'inbox.org' and heading['level'] == 2:
                # Level 2 under * Inbox are inbox entries
                inbox_entries.append({
                    'line_number': task['line_number'],
                    'text': task['title'],
                    'raw_content': line,
                    'processed': heading['state'] == 'DONE',
                    'properties': task['properties'],
                })

        idx += 1

    return {
        'tasks': tasks,
        'projects': projects,
        'inbox_entries': inbox_entries,
        'file_checksum': file_checksum,
        'source_file': str(file_path),
    }


def get_space_from_path(path: Path) -> str:
    """Determine which space a file belongs to."""
    path_str = str(path)
    for space, config in SPACES.items():
        if str(config['path']) in path_str:
            return space
    return 'personal'


def index_org_file(file_path: Path, space: str = None) -> Dict[str, int]:
    """Parse and index an org file to the database.

    Returns dict with counts: tasks, projects, inbox_entries
    """
    if space is None:
        space = get_space_from_path(file_path)

    parsed = parse_org_file(file_path, space)

    conn = get_connection(space)
    cursor = conn.cursor()

    source_file = str(file_path)
    now = datetime.now().isoformat()

    # Clear existing entries for this file
    cursor.execute("DELETE FROM tasks WHERE source_file = ?", (source_file,))
    cursor.execute("DELETE FROM projects WHERE source_file = ?", (source_file,))
    cursor.execute("DELETE FROM inbox_entries WHERE source_file = ?", (source_file,))

    # Index tasks
    task_id_map = {}  # line_number -> db_id
    for i, task in enumerate(parsed['tasks']):
        cursor.execute("""
            INSERT INTO tasks
            (state, heading, level, priority, scheduled, deadline, closed_at,
             category, effort, tags, properties, space, source_file, line_number,
             checksum, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task['state'],
            task['title'],
            task['level'],
            task['priority'],
            task['scheduled'],
            task['deadline'],
            task['closed'],
            task['category'],
            task['effort'],
            task['tags'],
            str(task['properties']) if task['properties'] else None,
            space,
            source_file,
            task['line_number'],
            parsed['file_checksum'],
            now,
            now
        ))
        task_id_map[task['line_number']] = cursor.lastrowid

    # Update parent references
    for task in parsed['tasks']:
        if task['parent_index'] is not None:
            parent_task = parsed['tasks'][task['parent_index']]
            parent_id = task_id_map.get(parent_task['line_number'])
            if parent_id:
                cursor.execute("""
                    UPDATE tasks SET parent_id = ? WHERE line_number = ? AND source_file = ?
                """, (parent_id, task['line_number'], source_file))

    # Index projects
    for project in parsed['projects']:
        cursor.execute("""
            INSERT INTO projects
            (name, status, category, space, source_file, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            project['name'],
            project['status'],
            project['category'],
            space,
            source_file,
            now,
            now
        ))

    # Index inbox entries
    for entry in parsed['inbox_entries']:
        cursor.execute("""
            INSERT INTO inbox_entries
            (text, raw_content, processed, space, source_file, line_number, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            entry['text'],
            entry['raw_content'],
            1 if entry['processed'] else 0,
            space,
            source_file,
            entry['line_number'],
            now
        ))

    # Update file checksum
    cursor.execute("""
        INSERT OR REPLACE INTO file_checksums (path, checksum, indexed_at, modified_at)
        VALUES (?, ?, ?, ?)
    """, (
        source_file,
        parsed['file_checksum'],
        now,
        datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
    ))

    conn.commit()
    conn.close()

    return {
        'tasks': len(parsed['tasks']),
        'projects': len(parsed['projects']),
        'inbox_entries': len(parsed['inbox_entries']),
    }


def scan_org_files(space: str, verbose: bool = True) -> Dict[str, int]:
    """Scan all org files in a space."""
    if space not in SPACES:
        print(f"Unknown space: {space}")
        return {'tasks': 0, 'projects': 0, 'inbox_entries': 0}

    org_paths = SPACES[space].get('org_paths', [])

    totals = {'tasks': 0, 'projects': 0, 'inbox_entries': 0}

    for org_path in org_paths:
        if not org_path.exists():
            if verbose:
                print(f"  Skipping (not found): {org_path}")
            continue

        if verbose:
            print(f"\n  Scanning: {org_path.relative_to(DATA_ROOT)}")

        for file_path in org_path.glob('*.org'):
            if file_path.name.startswith('.'):
                continue

            try:
                counts = index_org_file(file_path, space)
                for key in totals:
                    totals[key] += counts[key]

                if verbose:
                    print(f"    {file_path.name}: {counts['tasks']} tasks, {counts['projects']} projects")
            except Exception as e:
                print(f"    Error processing {file_path.name}: {e}")

    return totals


def sync_org_to_db(space: str = None, full: bool = False) -> Dict[str, Any]:
    """Sync org files to database.

    Args:
        space: Specific space to sync, or None for all
        full: If True, re-index all files. If False, only changed files.

    Returns sync stats.
    """
    stats = {
        'spaces_synced': [],
        'files_scanned': 0,
        'files_updated': 0,
        'tasks': 0,
        'projects': 0,
        'inbox_entries': 0,
    }

    spaces_to_sync = [space] if space else list(SPACES.keys())

    for sp in spaces_to_sync:
        if sp not in SPACES:
            continue

        # Ensure DB is initialized
        init_database(sp)

        org_paths = SPACES[sp].get('org_paths', [])

        for org_path in org_paths:
            if not org_path.exists():
                continue

            for file_path in org_path.glob('*.org'):
                if file_path.name.startswith('.'):
                    continue

                stats['files_scanned'] += 1

                # Check if file changed (unless full sync)
                if not full:
                    conn = get_connection(sp)
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT checksum FROM file_checksums WHERE path = ?",
                        (str(file_path),)
                    )
                    row = cursor.fetchone()
                    conn.close()

                    if row:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            current_checksum = compute_checksum(f.read())
                        if row['checksum'] == current_checksum:
                            continue  # File unchanged

                # Index the file
                try:
                    counts = index_org_file(file_path, sp)
                    stats['files_updated'] += 1
                    stats['tasks'] += counts['tasks']
                    stats['projects'] += counts['projects']
                    stats['inbox_entries'] += counts['inbox_entries']
                except Exception as e:
                    print(f"Error indexing {file_path}: {e}")

        stats['spaces_synced'].append(sp)

    return stats


def get_ai_tasks(space: str = None) -> List[Dict[str, Any]]:
    """Get all tasks tagged with :AI: that are TODO or NEXT."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, state, heading, priority, scheduled, deadline, category,
               tags, properties, space, source_file, line_number
        FROM tasks
        WHERE tags LIKE '%:AI:%'
          AND state IN ('TODO', 'NEXT')
        ORDER BY
            CASE priority WHEN 'A' THEN 1 WHEN 'B' THEN 2 WHEN 'C' THEN 3 ELSE 4 END,
            scheduled ASC NULLS LAST
    """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_inbox_entries(space: str = None, processed: bool = None) -> List[Dict[str, Any]]:
    """Get inbox entries, optionally filtered by processed status."""
    conn = get_connection(space)
    cursor = conn.cursor()

    if processed is None:
        cursor.execute("SELECT * FROM inbox_entries ORDER BY created_at DESC")
    else:
        cursor.execute(
            "SELECT * FROM inbox_entries WHERE processed = ? ORDER BY created_at DESC",
            (1 if processed else 0,)
        )

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_tasks_by_state(state: str, space: str = None) -> List[Dict[str, Any]]:
    """Get tasks by state (TODO, NEXT, WAITING, DONE)."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM tasks WHERE state = ? ORDER BY priority, scheduled
    """, (state,))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_tasks_by_category(category: str, space: str = None) -> List[Dict[str, Any]]:
    """Get tasks by category."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM tasks WHERE category = ? ORDER BY state, priority
    """, (category,))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def print_stats(stats: Dict[str, Any]):
    """Print sync statistics."""
    print(f"\n{'='*50}")
    print("ORG-MODE SYNC COMPLETE")
    print(f"{'='*50}")
    print(f"Spaces synced: {', '.join(stats['spaces_synced'])}")
    print(f"Files scanned: {stats['files_scanned']}")
    print(f"Files updated: {stats['files_updated']}")
    print(f"Tasks indexed: {stats['tasks']}")
    print(f"Projects indexed: {stats['projects']}")
    print(f"Inbox entries: {stats['inbox_entries']}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Org-mode Parser")
    parser.add_argument('path', nargs='?', help='File to process')
    parser.add_argument('--scan', action='store_true', help='Scan space for org files')
    parser.add_argument('--sync', action='store_true', help='Sync org files to DB')
    parser.add_argument('--full', action='store_true', help='Full sync (re-index all)')
    parser.add_argument('--space', '-s', choices=list(SPACES.keys()), help='Space to operate on')
    parser.add_argument('--ai-tasks', action='store_true', help='List AI-tagged tasks')
    parser.add_argument('--inbox', action='store_true', help='List inbox entries')

    args = parser.parse_args()

    if args.sync:
        stats = sync_org_to_db(args.space, args.full)
        print_stats(stats)

    elif args.scan:
        if not args.space:
            print("Usage: python org_parser.py --scan --space SPACE")
            sys.exit(1)
        init_database(args.space)
        totals = scan_org_files(args.space)
        print(f"\nTotal: {totals['tasks']} tasks, {totals['projects']} projects")

    elif args.ai_tasks:
        tasks = get_ai_tasks(args.space)
        print(f"\n=== AI Tasks ({len(tasks)}) ===")
        for t in tasks:
            priority = f"[#{t['priority']}]" if t['priority'] else ""
            print(f"  {t['state']} {priority} {t['heading']}")
            if t['scheduled']:
                print(f"       SCHEDULED: {t['scheduled']}")

    elif args.inbox:
        entries = get_inbox_entries(args.space, processed=False)
        print(f"\n=== Unprocessed Inbox ({len(entries)}) ===")
        for e in entries:
            print(f"  - {e['text']}")

    elif args.path:
        file_path = Path(args.path)
        if not file_path.exists():
            print(f"File not found: {args.path}")
            sys.exit(1)

        init_database(args.space)
        counts = index_org_file(file_path, args.space)
        print(f"\nIndexed {file_path.name}:")
        print(f"  Tasks: {counts['tasks']}")
        print(f"  Projects: {counts['projects']}")
        print(f"  Inbox entries: {counts['inbox_entries']}")

    else:
        parser.print_help()
