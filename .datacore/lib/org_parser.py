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
import json
import fcntl
import hashlib
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Optional, Dict, List, Any, Tuple

# Add lib to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from zettel_db import (
    get_connection, init_database, SPACES, DATA_ROOT, SYSTEM_PATHS
)


# Org-mode regex patterns
TODO_STATES = ['TODO', 'NEXT', 'WAITING', 'DONE', 'CANCELLED', 'DEFERRED', 'PROJECT']
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


def query_tasks(
    space: str = None,
    states: List[str] = None,
    tags: str = None,
    focus_area: str = None,
    deadline_within_days: int = None,
) -> List[Dict[str, Any]]:
    """Query tasks with flexible filters.

    Returns list of dicts with: level, state, title, tags, properties,
    space, focus_area (category), parent_heading, deadline, effort, closed_date.
    """
    conn = get_connection(space)
    cursor = conn.cursor()

    conditions = []
    params = []

    if states:
        placeholders = ','.join('?' for _ in states)
        conditions.append(f"t.state IN ({placeholders})")
        params.extend(states)

    if tags:
        conditions.append("t.tags LIKE ?")
        params.append(f'%{tags}%')

    if focus_area:
        conditions.append("t.category = ?")
        params.append(focus_area)

    if deadline_within_days is not None:
        conditions.append(
            "t.deadline IS NOT NULL AND t.deadline <= date('now', '+' || ? || ' days')"
        )
        params.append(str(deadline_within_days))

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    sql = f"""
        SELECT t.level, t.state, t.heading AS title, t.tags, t.properties,
               t.space, t.category AS focus_area, t.deadline, t.effort,
               t.closed_at AS closed_date, t.scheduled, t.priority,
               t.source_file, t.line_number,
               p.heading AS parent_heading
        FROM tasks t
        LEFT JOIN tasks p ON t.parent_id = p.id
        {where}
        ORDER BY
            CASE t.priority WHEN 'A' THEN 1 WHEN 'B' THEN 2 WHEN 'C' THEN 3 ELSE 4 END,
            t.deadline ASC NULLS LAST,
            t.scheduled ASC NULLS LAST
    """

    cursor.execute(sql, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def parse_deadlines(file_path: Path) -> List[Dict[str, Any]]:
    """Parse all tasks with DEADLINE from a file.

    Returns list of dicts with: heading, deadline_date, warning_days, state, line_number.
    """
    results = []
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.read().split('\n')

    current_heading = None
    current_state = None
    current_line = 0

    for i, line in enumerate(lines):
        heading = parse_heading(line)
        if heading and heading['state']:
            current_heading = heading['title']
            current_state = heading['state']
            current_line = i + 1

        deadline_match = DEADLINE_PATTERN.search(line)
        if deadline_match and current_heading:
            deadline_str = deadline_match.group(1)
            # Extract date part (YYYY-MM-DD)
            date_match = re.match(r'(\d{4}-\d{2}-\d{2})', deadline_str)
            if date_match:
                # Check for warning days in properties (scan ahead)
                warning_days = 14  # default
                for j in range(i + 1, min(i + 15, len(lines))):
                    prop_line = lines[j].strip()
                    if prop_line == ':END:':
                        break
                    warn_match = re.match(r':DEADLINE_WARNING_DAYS:\s+(\d+)', prop_line)
                    if warn_match:
                        warning_days = int(warn_match.group(1))
                        break

                results.append({
                    'heading': current_heading,
                    'deadline_date': date_match.group(1),
                    'warning_days': warning_days,
                    'state': current_state,
                    'line_number': current_line,
                })

    return results


def write_clock_entry(
    file_path: Path,
    heading_line_num: int,
    start: str,
    end: str,
) -> Dict[str, Any]:
    """Write a CLOCK entry to a task's LOGBOOK drawer.

    Args:
        file_path: Path to the org file
        heading_line_num: 1-based line number of the heading
        start: ISO datetime string (YYYY-MM-DDTHH:MM)
        end: ISO datetime string (YYYY-MM-DDTHH:MM)

    Returns dict with success status.
    """
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    duration = end_dt - start_dt
    hours = int(duration.total_seconds() // 3600)
    minutes = int((duration.total_seconds() % 3600) // 60)

    start_day = days[start_dt.weekday()]
    end_day = days[end_dt.weekday()]

    clock_line = (
        f"CLOCK: [{start_dt.strftime('%Y-%m-%d')} {start_day} "
        f"{start_dt.strftime('%H:%M')}]--"
        f"[{end_dt.strftime('%Y-%m-%d')} {end_day} "
        f"{end_dt.strftime('%H:%M')}] => "
        f" {hours}:{minutes:02d}"
    )

    with open(file_path, 'r+', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            lines = f.read().split('\n')
            idx = heading_line_num  # 0-based index after the heading

            # Find or create :LOGBOOK: drawer
            logbook_start = None
            logbook_end = None
            for j in range(idx, min(idx + 30, len(lines))):
                stripped = lines[j].strip()
                if stripped == ':LOGBOOK:':
                    logbook_start = j
                elif stripped == ':END:' and logbook_start is not None:
                    logbook_end = j
                    break
                elif re.match(r'^\*+\s', lines[j]):
                    break  # hit next heading

            if logbook_start is not None and logbook_end is not None:
                # Insert after :LOGBOOK: line (newest first)
                lines.insert(logbook_start + 1, clock_line)
            else:
                # Create :LOGBOOK: drawer after heading (and any planning/properties)
                insert_at = idx
                for j in range(idx, min(idx + 20, len(lines))):
                    stripped = lines[j].strip()
                    if stripped.startswith('SCHEDULED:') or stripped.startswith('DEADLINE:') or stripped.startswith('CLOSED:'):
                        insert_at = j + 1
                    elif stripped == ':PROPERTIES:':
                        # Skip to :END:
                        for k in range(j + 1, min(j + 50, len(lines))):
                            if lines[k].strip() == ':END:':
                                insert_at = k + 1
                                break
                        break
                    elif stripped == '' or re.match(r'^\*+\s', lines[j]):
                        break
                    else:
                        insert_at = j + 1

                lines.insert(insert_at, ':LOGBOOK:')
                lines.insert(insert_at + 1, clock_line)
                lines.insert(insert_at + 2, ':END:')

            f.seek(0)
            f.truncate()
            f.write('\n'.join(lines))
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    return {'success': True, 'clock_line': clock_line}


def archive_task(
    source_path: Path,
    heading_line_num: int,
    target_path: Path = None,
) -> Dict[str, Any]:
    """Archive a task from source file to archive file.

    Args:
        source_path: Path to the source org file
        heading_line_num: 1-based line number of the heading to archive
        target_path: Archive file path (default: source_path + '_archive')

    Returns dict with success status.
    """
    if target_path is None:
        target_path = Path(str(source_path) + '_archive')

    with open(source_path, 'r+', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            lines = f.read().split('\n')
            idx = heading_line_num - 1  # Convert to 0-based

            if idx >= len(lines):
                return {'error': f'Line {heading_line_num} out of range'}

            # Determine heading level
            heading = parse_heading(lines[idx])
            if not heading:
                return {'error': f'No heading at line {heading_line_num}'}

            level = heading['level']

            # Extract the full task block (heading + all content until next heading at same or higher level)
            block_end = idx + 1
            for j in range(idx + 1, len(lines)):
                next_heading = parse_heading(lines[j])
                if next_heading and next_heading['level'] <= level:
                    break
                block_end = j + 1

            task_block = lines[idx:block_end]

            # Add :ARCHIVE_TIME: property
            archive_time = datetime.now().strftime('[%Y-%m-%d %a %H:%M]')
            archive_prop = f':ARCHIVE_TIME: {archive_time}'

            # Find or create properties drawer in the block
            props_start = None
            props_end = None
            for j in range(1, len(task_block)):
                stripped = task_block[j].strip()
                if stripped == ':PROPERTIES:':
                    props_start = j
                elif stripped == ':END:' and props_start is not None:
                    props_end = j
                    break
                elif re.match(r'^\*+\s', task_block[j]):
                    break

            if props_start is not None and props_end is not None:
                task_block.insert(props_end, archive_prop)
            else:
                # Insert properties drawer after heading
                insert_at = 1
                for j in range(1, min(len(task_block), 5)):
                    stripped = task_block[j].strip()
                    if stripped.startswith('SCHEDULED:') or stripped.startswith('DEADLINE:') or stripped.startswith('CLOSED:'):
                        insert_at = j + 1
                    else:
                        break
                task_block.insert(insert_at, ':PROPERTIES:')
                task_block.insert(insert_at + 1, archive_prop)
                task_block.insert(insert_at + 2, ':END:')

            # Remove from source
            del lines[idx:block_end]
            f.seek(0)
            f.truncate()
            f.write('\n'.join(lines))
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    # Append to archive file
    archive_content = '\n'.join(task_block) + '\n'
    with open(target_path, 'a', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write('\n' + archive_content)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    return {
        'success': True,
        'archived_heading': heading['title'],
        'source': str(source_path),
        'target': str(target_path),
        'lines_archived': len(task_block),
    }


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
    subparsers = parser.add_subparsers(dest='command')

    # Legacy positional arg for backward compat
    parser.add_argument('path', nargs='?', help='File to process (legacy)')
    parser.add_argument('--scan', action='store_true', help='Scan space for org files')
    parser.add_argument('--sync', action='store_true', help='Sync org files to DB')
    parser.add_argument('--full', action='store_true', help='Full sync (re-index all)')
    parser.add_argument('--space', '-s', help='Space to operate on')
    parser.add_argument('--ai-tasks', action='store_true', help='List AI-tagged tasks')
    parser.add_argument('--inbox', action='store_true', help='List inbox entries')
    parser.add_argument('--json', action='store_true', help='Output as JSON')

    # query subcommand
    query_parser = subparsers.add_parser('query', help='Query tasks from DB')
    query_parser.add_argument('--json', action='store_true', default=True)
    query_parser.add_argument('--states', help='Comma-separated states (e.g., TODO,NEXT)')
    query_parser.add_argument('--tags', help='Tag filter (e.g., :AI:)')
    query_parser.add_argument('--focus-area', help='Category/focus area filter')
    query_parser.add_argument('--deadline-within', type=int, help='Deadline within N days')
    query_parser.add_argument('--space', '-s', help='Space to query')

    # deadlines subcommand
    dl_parser = subparsers.add_parser('deadlines', help='List deadlines from file')
    dl_parser.add_argument('--json', action='store_true', default=True)
    dl_parser.add_argument('--days', type=int, default=14, help='Show deadlines within N days')
    dl_parser.add_argument('--file', required=True, help='Org file to scan')

    # write-clock subcommand
    wc_parser = subparsers.add_parser('write-clock', help='Write CLOCK entry')
    wc_parser.add_argument('--file', required=True, help='Org file path')
    wc_parser.add_argument('--heading-line', type=int, required=True, help='1-based heading line number')
    wc_parser.add_argument('--start', required=True, help='Start ISO datetime (YYYY-MM-DDTHH:MM)')
    wc_parser.add_argument('--end', required=True, help='End ISO datetime (YYYY-MM-DDTHH:MM)')

    # archive subcommand
    ar_parser = subparsers.add_parser('archive', help='Archive a task')
    ar_parser.add_argument('--source', required=True, help='Source org file path')
    ar_parser.add_argument('--heading-line', type=int, required=True, help='1-based heading line number')
    ar_parser.add_argument('--target', help='Target archive file (default: source_archive)')

    args = parser.parse_args()

    # --- Subcommand handlers ---
    if args.command == 'query':
        states = args.states.split(',') if args.states else None
        results = query_tasks(
            space=args.space,
            states=states,
            tags=args.tags,
            focus_area=args.focus_area,
            deadline_within_days=args.deadline_within,
        )
        print(json.dumps(results, default=str))

    elif args.command == 'deadlines':
        fp = Path(args.file)
        if not fp.exists():
            print(json.dumps({'error': f'File not found: {args.file}'}))
            sys.exit(1)
        deadlines = parse_deadlines(fp)
        # Filter by days
        today = date.today()
        cutoff = today + timedelta(days=args.days)
        filtered = []
        for d in deadlines:
            try:
                dl_date = date.fromisoformat(d['deadline_date'])
                if dl_date <= cutoff:
                    d['overdue'] = dl_date < today
                    d['days_remaining'] = (dl_date - today).days
                    filtered.append(d)
            except ValueError:
                pass
        print(json.dumps(filtered, default=str))

    elif args.command == 'write-clock':
        fp = Path(args.file)
        if not fp.exists():
            print(json.dumps({'error': f'File not found: {args.file}'}))
            sys.exit(1)
        result = write_clock_entry(fp, args.heading_line, args.start, args.end)
        print(json.dumps(result, default=str))

    elif args.command == 'archive':
        source = Path(args.source)
        if not source.exists():
            print(json.dumps({'error': f'File not found: {args.source}'}))
            sys.exit(1)
        target = Path(args.target) if args.target else None
        result = archive_task(source, args.heading_line, target)
        print(json.dumps(result, default=str))

    # --- Legacy CLI handlers ---
    elif args.sync:
        stats = sync_org_to_db(args.space, args.full)
        if getattr(args, 'json', False):
            print(json.dumps(stats, default=str))
        else:
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
        if getattr(args, 'json', False):
            print(json.dumps(tasks, default=str))
        else:
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
