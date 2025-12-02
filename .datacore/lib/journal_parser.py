#!/usr/bin/env python3
"""
Journal Parser (DIP-0004)

Parses journal files to extract:
- Daily journal entries
- Work sessions (## Session: headers)
- Goals and accomplishments
- Files modified
- Decisions made
- Trading entries

Core Principle: Parse journal files, index to DB for querying.

Usage:
    python journal_parser.py <file_path>
    python journal_parser.py --scan --space SPACE
    python journal_parser.py --sync [--space SPACE]
    python journal_parser.py --sessions --date YYYY-MM-DD
"""

import re
import sys
import yaml
import hashlib
from pathlib import Path
from datetime import datetime, date
from typing import Optional, Dict, List, Any, Tuple

# Add lib to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from zettel_db import (
    get_connection, init_database, SPACES, DATA_ROOT
)


def compute_checksum(content: str) -> str:
    """Compute MD5 checksum of content."""
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Extract YAML frontmatter from markdown content."""
    if not content.startswith('---'):
        return {}, content

    parts = content.split('---', 2)
    if len(parts) < 3:
        return {}, content

    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        frontmatter = {}

    body = parts[2].strip()
    return frontmatter, body


def extract_date_from_filename(filename: str) -> Optional[str]:
    """Extract date from journal filename (YYYY-MM-DD.md)."""
    match = re.match(r'^(\d{4}-\d{2}-\d{2})\.md$', filename)
    if match:
        return match.group(1)
    return None


def extract_sessions(content: str) -> List[Dict[str, Any]]:
    """Extract sessions from journal content.

    Sessions are marked by ## Session: headers.
    """
    sessions = []

    # Pattern for session headers
    session_pattern = re.compile(r'^##\s+Session:\s*(.+)$', re.MULTILINE)

    # Find all session headers with their positions
    matches = list(session_pattern.finditer(content))

    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start_pos = match.end()

        # Find end of session (next ## header or end of content)
        if i + 1 < len(matches):
            end_pos = matches[i + 1].start()
        else:
            # Find next ## header that's not a sub-section
            next_header = re.search(r'^##\s+(?!#)', content[start_pos:], re.MULTILINE)
            if next_header:
                end_pos = start_pos + next_header.start()
            else:
                end_pos = len(content)

        session_content = content[start_pos:end_pos].strip()

        session = {
            'title': title,
            'content': session_content,
            'goal': extract_goal(session_content),
            'accomplishments': extract_accomplishments(session_content),
            'files_modified': extract_files_modified(session_content),
            'session_type': detect_session_type(title, session_content),
        }

        sessions.append(session)

    return sessions


def extract_goal(content: str) -> Optional[str]:
    """Extract goal from session content.

    Looks for **Goal:** pattern.
    """
    goal_pattern = re.compile(r'\*\*Goal:\*\*\s*(.+?)(?:\n\n|\n\*\*|$)', re.DOTALL)
    match = goal_pattern.search(content)
    if match:
        return match.group(1).strip()
    return None


def extract_accomplishments(content: str) -> List[str]:
    """Extract accomplishments from session content.

    Looks for **Accomplished:** or **Accomplishments:** section with bullet points.
    """
    accomplishments = []

    # Find accomplishments section
    pattern = re.compile(
        r'\*\*(?:Accomplished|Accomplishments|Done):\*\*\s*\n((?:[-*]\s+.+\n?)+)',
        re.MULTILINE | re.IGNORECASE
    )

    match = pattern.search(content)
    if match:
        items_text = match.group(1)
        # Extract bullet points
        for line in items_text.split('\n'):
            line = line.strip()
            if line.startswith('-') or line.startswith('*'):
                item = line[1:].strip()
                if item:
                    accomplishments.append(item)

    return accomplishments


def extract_files_modified(content: str) -> List[Dict[str, str]]:
    """Extract files modified from session content.

    Looks for **Files Modified:** or **Changes:** section.
    """
    files = []

    # Find files modified section
    pattern = re.compile(
        r'\*\*(?:Files Modified|Files Changed|Changes):\*\*\s*\n((?:[-*]\s+.+\n?)+)',
        re.MULTILINE | re.IGNORECASE
    )

    match = pattern.search(content)
    if match:
        items_text = match.group(1)
        for line in items_text.split('\n'):
            line = line.strip()
            if line.startswith('-') or line.startswith('*'):
                file_path = line[1:].strip()
                # Remove backticks if present
                file_path = file_path.strip('`')
                if file_path:
                    # Try to detect change type from content
                    change_type = 'modified'
                    if 'create' in line.lower() or 'new' in line.lower():
                        change_type = 'created'
                    elif 'delete' in line.lower() or 'remove' in line.lower():
                        change_type = 'deleted'

                    files.append({
                        'file_path': file_path,
                        'change_type': change_type
                    })

    return files


def detect_session_type(title: str, content: str) -> str:
    """Detect session type based on title and content."""
    title_lower = title.lower()
    content_lower = content.lower()

    if any(word in title_lower for word in ['trading', 'market', 'position']):
        return 'trading'
    elif any(word in title_lower for word in ['meeting', 'call', 'standup']):
        return 'meeting'
    elif any(word in title_lower for word in ['research', 'reading', 'learning']):
        return 'research'
    elif any(word in title_lower for word in ['review', 'weekly', 'monthly']):
        return 'review'
    elif any(word in content_lower for word in ['def ', 'class ', 'function', 'import ']):
        return 'coding'
    elif any(word in title_lower for word in ['write', 'draft', 'blog', 'content']):
        return 'writing'
    else:
        return 'general'


def extract_decisions(content: str) -> List[Dict[str, str]]:
    """Extract decisions from journal content.

    Looks for **Decision:** or decisions section.
    """
    decisions = []

    # Pattern for inline decisions
    decision_pattern = re.compile(
        r'\*\*Decision:\*\*\s*(.+?)(?:\n\n|\n\*\*|$)',
        re.DOTALL
    )

    for match in decision_pattern.finditer(content):
        decision_text = match.group(1).strip()
        decisions.append({
            'description': decision_text,
            'rationale': None,  # Could be extracted if formatted
            'reversible': True
        })

    return decisions


def extract_trading_data(content: str) -> Optional[Dict[str, Any]]:
    """Extract trading-related data from journal content.

    Looks for trading metrics like emotional state, PnL, IMR, etc.
    """
    trading_data = {}

    # Emotional state (1-10)
    emotional_match = re.search(r'emotional\s*(?:state|distress)?[:\s]*(\d+)/10', content, re.IGNORECASE)
    if emotional_match:
        trading_data['emotional_state'] = int(emotional_match.group(1))

    # IMR
    imr_match = re.search(r'IMR[:\s]*(\d+(?:\.\d+)?)\s*%?', content, re.IGNORECASE)
    if imr_match:
        trading_data['imr'] = float(imr_match.group(1))

    # PHS
    phs_match = re.search(r'PHS[:\s]*(\d+)', content, re.IGNORECASE)
    if phs_match:
        trading_data['phs'] = int(phs_match.group(1))

    # PnL - require colon/$ after keyword and at least one digit
    pnl_match = re.search(r'(?:realized|P&?L)\s*[:\$]\s*\$?([-]?\d[\d,]*(?:\.\d+)?)', content, re.IGNORECASE)
    if pnl_match:
        pnl_str = pnl_match.group(1).replace(',', '')
        if pnl_str and pnl_str not in ['-', '']:
            trading_data['pnl_realized'] = float(pnl_str)

    # Framework violations
    if 'framework violation' in content.lower():
        violations = []
        violation_pattern = re.compile(r'(?:violation|broke rule)[:\s]*(.+?)(?:\n|$)', re.IGNORECASE)
        for match in violation_pattern.finditer(content):
            violations.append(match.group(1).strip())
        if violations:
            trading_data['framework_violations'] = violations

    return trading_data if trading_data else None


def parse_journal_file(file_path: Path, space: str = None) -> Dict[str, Any]:
    """Parse a journal file.

    Returns dict with:
    - date: Journal date
    - type: journal or team-journal
    - sessions: List of session dicts
    - decisions: List of decision dicts
    - trading_data: Trading metrics if present
    - word_count: Total word count
    - file_checksum: MD5 of file content
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    frontmatter, body = parse_frontmatter(content)
    file_checksum = compute_checksum(content)

    # Determine date
    journal_date = frontmatter.get('date')
    if not journal_date:
        journal_date = extract_date_from_filename(file_path.name)
    if not journal_date:
        journal_date = datetime.now().strftime('%Y-%m-%d')

    # Determine type
    journal_type = frontmatter.get('type', 'journal')
    if 'team' in str(file_path).lower() or frontmatter.get('space'):
        journal_type = 'team-journal'

    # Extract sessions
    sessions = extract_sessions(body)

    # Extract decisions
    decisions = extract_decisions(body)

    # Extract trading data
    trading_data = extract_trading_data(body)

    return {
        'date': str(journal_date),
        'type': journal_type,
        'space': frontmatter.get('space', space),
        'content': body,
        'word_count': len(body.split()),
        'sessions': sessions,
        'session_count': len(sessions),
        'decisions': decisions,
        'trading_data': trading_data,
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


def index_journal_file(file_path: Path, space: str = None) -> Dict[str, int]:
    """Parse and index a journal file to the database.

    Returns dict with counts.
    """
    if space is None:
        space = get_space_from_path(file_path)

    parsed = parse_journal_file(file_path, space)

    conn = get_connection(space)
    cursor = conn.cursor()

    source_file = str(file_path)
    now = datetime.now().isoformat()

    # Clear existing entries for this file
    cursor.execute("DELETE FROM journal_entries WHERE source_file = ?", (source_file,))

    # Index journal entry
    cursor.execute("""
        INSERT INTO journal_entries
        (date, space, type, content, word_count, session_count, source_file, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        parsed['date'],
        parsed['space'] or space,
        parsed['type'],
        parsed['content'],
        parsed['word_count'],
        parsed['session_count'],
        source_file,
        now,
        now
    ))
    journal_id = cursor.lastrowid

    # Index sessions
    for session in parsed['sessions']:
        cursor.execute("""
            INSERT INTO sessions
            (journal_id, title, goal, space, session_type, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            journal_id,
            session['title'],
            session['goal'],
            parsed['space'] or space,
            session['session_type'],
            session['content'],
            now
        ))
        session_id = cursor.lastrowid

        # Index accomplishments
        for accomplishment in session['accomplishments']:
            cursor.execute("""
                INSERT INTO accomplishments (session_id, description, created_at)
                VALUES (?, ?, ?)
            """, (session_id, accomplishment, now))

        # Index files modified
        for file_info in session['files_modified']:
            cursor.execute("""
                INSERT INTO files_modified (session_id, file_path, change_type, created_at)
                VALUES (?, ?, ?, ?)
            """, (session_id, file_info['file_path'], file_info['change_type'], now))

    # Index decisions
    for decision in parsed['decisions']:
        cursor.execute("""
            INSERT INTO decisions
            (file_id, description, rationale, reversible, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            None,  # No file reference for journal decisions
            decision['description'],
            decision.get('rationale'),
            1 if decision.get('reversible', True) else 0,
            now
        ))

    # Index trading data if present
    if parsed['trading_data']:
        td = parsed['trading_data']
        cursor.execute("""
            INSERT INTO trading_entries
            (journal_id, date, emotional_state, framework_violations,
             pnl_realized, imr, phs, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            journal_id,
            parsed['date'],
            td.get('emotional_state'),
            str(td.get('framework_violations')) if td.get('framework_violations') else None,
            td.get('pnl_realized'),
            td.get('imr'),
            td.get('phs'),
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
        'sessions': len(parsed['sessions']),
        'decisions': len(parsed['decisions']),
        'has_trading': 1 if parsed['trading_data'] else 0,
    }


def scan_journal_files(space: str, verbose: bool = True) -> Dict[str, int]:
    """Scan all journal files in a space."""
    if space not in SPACES:
        print(f"Unknown space: {space}")
        return {'journals': 0, 'sessions': 0}

    journal_path = SPACES[space].get('journal_path')

    totals = {'journals': 0, 'sessions': 0, 'decisions': 0}

    if not journal_path or not journal_path.exists():
        if verbose:
            print(f"  No journal path for {space}")
        return totals

    if verbose:
        print(f"\n  Scanning: {journal_path.relative_to(DATA_ROOT)}")

    for file_path in sorted(journal_path.glob('*.md')):
        if file_path.name.startswith('.'):
            continue

        # Check if filename matches date pattern
        if not extract_date_from_filename(file_path.name):
            continue

        try:
            counts = index_journal_file(file_path, space)
            totals['journals'] += 1
            totals['sessions'] += counts['sessions']
            totals['decisions'] += counts['decisions']

            if verbose and counts['sessions'] > 0:
                print(f"    {file_path.name}: {counts['sessions']} sessions")
        except Exception as e:
            print(f"    Error processing {file_path.name}: {e}")

    return totals


def sync_journals_to_db(space: str = None, full: bool = False) -> Dict[str, Any]:
    """Sync journal files to database.

    Args:
        space: Specific space to sync, or None for all
        full: If True, re-index all files. If False, only changed files.

    Returns sync stats.
    """
    stats = {
        'spaces_synced': [],
        'files_scanned': 0,
        'files_updated': 0,
        'journals': 0,
        'sessions': 0,
    }

    spaces_to_sync = [space] if space else list(SPACES.keys())

    for sp in spaces_to_sync:
        if sp not in SPACES:
            continue

        # Ensure DB is initialized
        init_database(sp)

        journal_path = SPACES[sp].get('journal_path')

        if not journal_path or not journal_path.exists():
            continue

        for file_path in journal_path.glob('*.md'):
            if file_path.name.startswith('.'):
                continue

            if not extract_date_from_filename(file_path.name):
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
                counts = index_journal_file(file_path, sp)
                stats['files_updated'] += 1
                stats['journals'] += 1
                stats['sessions'] += counts['sessions']
            except Exception as e:
                print(f"Error indexing {file_path}: {e}")

        stats['spaces_synced'].append(sp)

    return stats


def get_sessions_by_date(journal_date: str, space: str = None) -> List[Dict[str, Any]]:
    """Get all sessions for a specific date."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.*, j.date
        FROM sessions s
        JOIN journal_entries j ON s.journal_id = j.id
        WHERE j.date = ?
        ORDER BY s.id
    """, (journal_date,))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_recent_sessions(days: int = 7, space: str = None) -> List[Dict[str, Any]]:
    """Get sessions from the last N days."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.*, j.date
        FROM sessions s
        JOIN journal_entries j ON s.journal_id = j.id
        WHERE j.date >= date('now', ?)
        ORDER BY j.date DESC, s.id
    """, (f'-{days} days',))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_session_stats(date_from: str = None, date_to: str = None, space: str = None) -> Dict[str, Any]:
    """Get aggregate session statistics."""
    conn = get_connection(space)
    cursor = conn.cursor()

    where_clause = "1=1"
    params = []

    if date_from:
        where_clause += " AND j.date >= ?"
        params.append(date_from)
    if date_to:
        where_clause += " AND j.date <= ?"
        params.append(date_to)

    cursor.execute(f"""
        SELECT
            COUNT(DISTINCT j.id) as journal_count,
            COUNT(s.id) as session_count,
            SUM(j.word_count) as total_words
        FROM journal_entries j
        LEFT JOIN sessions s ON s.journal_id = j.id
        WHERE {where_clause}
    """, params)

    row = cursor.fetchone()

    stats = {
        'journal_count': row['journal_count'] or 0,
        'session_count': row['session_count'] or 0,
        'total_words': row['total_words'] or 0,
    }

    # Session types breakdown
    cursor.execute(f"""
        SELECT s.session_type, COUNT(*) as count
        FROM sessions s
        JOIN journal_entries j ON s.journal_id = j.id
        WHERE {where_clause}
        GROUP BY s.session_type
    """, params)

    stats['by_session_type'] = {row['session_type']: row['count'] for row in cursor.fetchall()}

    conn.close()
    return stats


def print_stats(stats: Dict[str, Any]):
    """Print sync statistics."""
    print(f"\n{'='*50}")
    print("JOURNAL SYNC COMPLETE")
    print(f"{'='*50}")
    print(f"Spaces synced: {', '.join(stats['spaces_synced'])}")
    print(f"Files scanned: {stats['files_scanned']}")
    print(f"Files updated: {stats['files_updated']}")
    print(f"Journals indexed: {stats['journals']}")
    print(f"Sessions indexed: {stats['sessions']}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Journal Parser")
    parser.add_argument('path', nargs='?', help='File to process')
    parser.add_argument('--scan', action='store_true', help='Scan space for journal files')
    parser.add_argument('--sync', action='store_true', help='Sync journal files to DB')
    parser.add_argument('--full', action='store_true', help='Full sync (re-index all)')
    parser.add_argument('--space', '-s', choices=list(SPACES.keys()), help='Space to operate on')
    parser.add_argument('--sessions', action='store_true', help='List sessions')
    parser.add_argument('--date', help='Filter by date (YYYY-MM-DD)')
    parser.add_argument('--stats', action='store_true', help='Show session statistics')

    args = parser.parse_args()

    if args.sync:
        stats = sync_journals_to_db(args.space, args.full)
        print_stats(stats)

    elif args.scan:
        if not args.space:
            print("Usage: python journal_parser.py --scan --space SPACE")
            sys.exit(1)
        init_database(args.space)
        totals = scan_journal_files(args.space)
        print(f"\nTotal: {totals['journals']} journals, {totals['sessions']} sessions")

    elif args.sessions:
        if args.date:
            sessions = get_sessions_by_date(args.date, args.space)
        else:
            sessions = get_recent_sessions(7, args.space)

        print(f"\n=== Sessions ({len(sessions)}) ===")
        for s in sessions:
            print(f"\n  [{s.get('date', 'N/A')}] {s['title']}")
            if s['goal']:
                print(f"    Goal: {s['goal'][:60]}...")
            print(f"    Type: {s['session_type']}")

    elif args.stats:
        stats = get_session_stats(space=args.space)
        print(f"\n=== Session Statistics ===")
        print(f"Journals: {stats['journal_count']}")
        print(f"Sessions: {stats['session_count']}")
        print(f"Total words: {stats['total_words']:,}")
        if stats['by_session_type']:
            print("\nBy type:")
            for stype, count in stats['by_session_type'].items():
                print(f"  {stype}: {count}")

    elif args.path:
        file_path = Path(args.path)
        if not file_path.exists():
            print(f"File not found: {args.path}")
            sys.exit(1)

        init_database(args.space)
        counts = index_journal_file(file_path, args.space)
        print(f"\nIndexed {file_path.name}:")
        print(f"  Sessions: {counts['sessions']}")
        print(f"  Decisions: {counts['decisions']}")

    else:
        parser.print_help()
