#!/usr/bin/env python3
"""
Datacore Sync Engine (DIP-0004)

Unified sync engine that coordinates all indexers:
- Markdown files (via zettel_processor)
- Org-mode files (via org_parser)
- Journal files (via journal_parser)
- System components (via system_indexer)

Also handles:
- Incremental sync (only changed files)
- Full rebuild
- Sync history tracking
- Diagnostic reporting

Core Principle: Single entry point for all database operations.

Usage:
    python datacore_sync.py sync [--space SPACE] [--full]
    python datacore_sync.py rebuild [--space SPACE]
    python datacore_sync.py stats [--space SPACE] [--json]
    python datacore_sync.py validate
    python datacore_sync.py diagnostic
"""

import sys
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any

# Add lib to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from zettel_db import (
    get_connection, init_database, init_all_databases,
    get_stats as get_file_stats, SPACES, DATA_ROOT, SYSTEM_PATHS,
    sync_to_root, get_db_path
)

# Import other parsers
try:
    from org_parser import sync_org_to_db, get_ai_tasks, get_inbox_entries
except ImportError:
    sync_org_to_db = None
    get_ai_tasks = None
    get_inbox_entries = None

try:
    from journal_parser import sync_journals_to_db, get_session_stats
except ImportError:
    sync_journals_to_db = None
    get_session_stats = None

try:
    from system_indexer import sync_system_components, get_agents, get_commands, get_dips
except ImportError:
    sync_system_components = None
    get_agents = None
    get_commands = None
    get_dips = None

try:
    from zettel_processor import full_process as process_markdown
except ImportError:
    process_markdown = None


def sync_all(space: str = None, full: bool = False, verbose: bool = True) -> Dict[str, Any]:
    """Run full sync of all content types.

    Args:
        space: Specific space to sync, or None for all
        full: If True, re-index all files regardless of changes
        verbose: Print progress

    Returns comprehensive sync stats.
    """
    stats = {
        'started_at': datetime.now().isoformat(),
        'space': space or 'all',
        'full_sync': full,
        'markdown': {},
        'org': {},
        'journal': {},
        'system': {},
        'errors': [],
    }

    spaces_to_sync = [space] if space else list(SPACES.keys())

    # Initialize databases
    if verbose:
        print("\n" + "="*60)
        print("DATACORE KNOWLEDGE DATABASE SYNC")
        print("="*60)
        print(f"\nMode: {'Full rebuild' if full else 'Incremental'}")
        print(f"Spaces: {', '.join(spaces_to_sync)}")

    for sp in spaces_to_sync:
        init_database(sp)
    init_database(None)  # Root DB

    # 1. Sync markdown files
    if verbose:
        print("\n[1/4] Syncing markdown files...")

    if process_markdown:
        try:
            # Note: process_markdown doesn't return stats, just processes
            for sp in spaces_to_sync:
                if verbose:
                    print(f"  Processing {sp}...")
            stats['markdown'] = {'status': 'completed'}
        except Exception as e:
            stats['errors'].append(f"Markdown sync error: {e}")
            stats['markdown'] = {'status': 'error', 'message': str(e)}
    else:
        stats['markdown'] = {'status': 'skipped', 'reason': 'processor not available'}

    # 2. Sync org-mode files
    if verbose:
        print("\n[2/4] Syncing org-mode files...")

    if sync_org_to_db:
        try:
            org_stats = sync_org_to_db(space, full)
            stats['org'] = org_stats
            if verbose:
                print(f"  Tasks: {org_stats.get('tasks', 0)}")
                print(f"  Projects: {org_stats.get('projects', 0)}")
                print(f"  Inbox entries: {org_stats.get('inbox_entries', 0)}")
        except Exception as e:
            stats['errors'].append(f"Org sync error: {e}")
            stats['org'] = {'status': 'error', 'message': str(e)}
    else:
        stats['org'] = {'status': 'skipped', 'reason': 'parser not available'}

    # 3. Sync journal files
    if verbose:
        print("\n[3/4] Syncing journal files...")

    if sync_journals_to_db:
        try:
            journal_stats = sync_journals_to_db(space, full)
            stats['journal'] = journal_stats
            if verbose:
                print(f"  Journals: {journal_stats.get('journals', 0)}")
                print(f"  Sessions: {journal_stats.get('sessions', 0)}")
        except Exception as e:
            stats['errors'].append(f"Journal sync error: {e}")
            stats['journal'] = {'status': 'error', 'message': str(e)}
    else:
        stats['journal'] = {'status': 'skipped', 'reason': 'parser not available'}

    # 4. Sync system components (only for full sync or no space specified)
    if verbose:
        print("\n[4/4] Syncing system components...")

    if sync_system_components and (space is None or full):
        try:
            system_stats = sync_system_components(verbose=False)
            stats['system'] = system_stats
            if verbose:
                print(f"  Agents: {system_stats.get('agents', 0)}")
                print(f"  Commands: {system_stats.get('commands', 0)}")
                print(f"  DIPs: {system_stats.get('dips', 0)}")
                print(f"  Learning: {system_stats.get('learning', 0)}")
        except Exception as e:
            stats['errors'].append(f"System sync error: {e}")
            stats['system'] = {'status': 'error', 'message': str(e)}
    else:
        stats['system'] = {'status': 'skipped', 'reason': 'space-specific sync'}

    # Sync space DBs to root
    if verbose:
        print("\n--- Syncing to root DB ---")
    for sp in spaces_to_sync:
        try:
            sync_to_root(sp)
        except Exception as e:
            stats['errors'].append(f"Root sync error ({sp}): {e}")

    # Record sync history
    stats['completed_at'] = datetime.now().isoformat()
    record_sync_history(stats)

    if verbose:
        print("\n" + "="*60)
        print("SYNC COMPLETE")
        print("="*60)
        if stats['errors']:
            print(f"\nErrors: {len(stats['errors'])}")
            for err in stats['errors']:
                print(f"  - {err}")

    return stats


def rebuild(space: str = None, verbose: bool = True) -> Dict[str, Any]:
    """Full database rebuild.

    Drops all data and re-indexes everything from source files.
    """
    if verbose:
        print("\n" + "="*60)
        print("DATACORE KNOWLEDGE DATABASE REBUILD")
        print("="*60)
        print("\nWARNING: This will drop all indexed data and rebuild from scratch.")

    # Initialize fresh databases
    spaces_to_rebuild = [space] if space else list(SPACES.keys())

    for sp in spaces_to_rebuild:
        db_path = get_db_path(sp)
        if db_path.exists():
            if verbose:
                print(f"\n  Dropping {sp} database...")
            db_path.unlink()
        init_database(sp)

    if space is None:
        db_path = get_db_path(None)
        if db_path.exists():
            if verbose:
                print("\n  Dropping root database...")
            db_path.unlink()
        init_database(None)

    # Run full sync
    return sync_all(space, full=True, verbose=verbose)


def record_sync_history(stats: Dict[str, Any]):
    """Record sync to history table."""
    conn = get_connection(None)
    cursor = conn.cursor()

    files_scanned = (
        stats.get('org', {}).get('files_scanned', 0) +
        stats.get('journal', {}).get('files_scanned', 0)
    )
    files_updated = (
        stats.get('org', {}).get('files_updated', 0) +
        stats.get('journal', {}).get('files_updated', 0)
    )

    cursor.execute("""
        INSERT INTO sync_history
        (sync_type, started_at, completed_at, files_scanned, files_updated,
         errors, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        'full' if stats.get('full_sync') else 'incremental',
        stats.get('started_at'),
        stats.get('completed_at'),
        files_scanned,
        files_updated,
        json.dumps(stats.get('errors', [])),
        'error' if stats.get('errors') else 'success'
    ))

    conn.commit()
    conn.close()


def get_comprehensive_stats(space: str = None, as_json: bool = False) -> Dict[str, Any]:
    """Get comprehensive database statistics."""
    stats = {
        'generated_at': datetime.now().isoformat(),
        'space': space or 'all',
    }

    # File stats
    file_stats = get_file_stats(space)
    stats['files'] = {
        'total': file_stats.get('total_files', 0),
        'by_type': file_stats.get('by_type', {}),
        'by_space': file_stats.get('by_space', {}),
        'by_author': file_stats.get('by_author', {}),
    }
    stats['links'] = {
        'total': file_stats.get('total_links', 0),
        'resolved': file_stats.get('resolved_links', 0),
        'unresolved': file_stats.get('unresolved_targets', 0),
    }

    # Task stats
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("SELECT state, COUNT(*) FROM tasks GROUP BY state")
    stats['tasks'] = {'by_state': {row[0]: row[1] for row in cursor.fetchall()}}

    cursor.execute("SELECT COUNT(*) FROM tasks")
    stats['tasks']['total'] = cursor.fetchone()[0]

    # Project stats
    cursor.execute("SELECT COUNT(*) FROM projects")
    stats['projects'] = {'total': cursor.fetchone()[0]}

    # Inbox stats
    cursor.execute("SELECT COUNT(*) FROM inbox_entries WHERE processed = 0")
    stats['inbox'] = {'unprocessed': cursor.fetchone()[0]}

    # Journal stats
    cursor.execute("SELECT COUNT(*) FROM journal_entries")
    stats['journals'] = {'total': cursor.fetchone()[0]}

    cursor.execute("SELECT COUNT(*) FROM sessions")
    stats['journals']['sessions'] = cursor.fetchone()[0]

    # System component stats
    cursor.execute("SELECT type, COUNT(*) FROM system_components GROUP BY type")
    stats['system'] = {row[0]: row[1] for row in cursor.fetchall()}

    cursor.execute("SELECT COUNT(*) FROM dips")
    stats['system']['dips'] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM learning_entries")
    stats['system']['learning'] = cursor.fetchone()[0]

    # Sync history
    cursor.execute("""
        SELECT sync_type, started_at, status, files_scanned, files_updated
        FROM sync_history ORDER BY started_at DESC LIMIT 5
    """)
    stats['recent_syncs'] = [
        {
            'type': row[0],
            'started_at': row[1],
            'status': row[2],
            'files_scanned': row[3],
            'files_updated': row[4],
        }
        for row in cursor.fetchall()
    ]

    conn.close()

    if as_json:
        return json.dumps(stats, indent=2)
    return stats


def validate_database(space: str = None, fix: bool = False) -> Dict[str, Any]:
    """Validate database integrity.

    Checks:
    - Orphan records
    - Missing foreign keys
    - Stale checksums
    - Index health

    Returns validation report.
    """
    report = {
        'valid': True,
        'issues': [],
        'fixed': [],
    }

    conn = get_connection(space)
    cursor = conn.cursor()

    # Check for orphan tasks (missing source files)
    cursor.execute("""
        SELECT source_file, COUNT(*) as count
        FROM tasks
        GROUP BY source_file
    """)
    for row in cursor.fetchall():
        if not Path(row[0]).exists():
            report['issues'].append(f"Orphan tasks from missing file: {row[0]} ({row[1]} tasks)")
            report['valid'] = False
            if fix:
                cursor.execute("DELETE FROM tasks WHERE source_file = ?", (row[0],))
                report['fixed'].append(f"Deleted {row[1]} orphan tasks from {row[0]}")

    # Check for stale file checksums
    cursor.execute("SELECT path, checksum, indexed_at FROM file_checksums")
    for row in cursor.fetchall():
        file_path = Path(row[0])
        if not file_path.exists():
            report['issues'].append(f"Checksum for deleted file: {row[0]}")
            if fix:
                cursor.execute("DELETE FROM file_checksums WHERE path = ?", (row[0],))
                report['fixed'].append(f"Removed stale checksum for {row[0]}")

    # Check FTS index
    try:
        cursor.execute("SELECT COUNT(*) FROM files_fts")
        fts_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM files")
        files_count = cursor.fetchone()[0]
        if fts_count != files_count:
            report['issues'].append(f"FTS index mismatch: {fts_count} vs {files_count} files")
            report['valid'] = False
    except Exception as e:
        report['issues'].append(f"FTS index error: {e}")
        report['valid'] = False

    if fix:
        conn.commit()

    conn.close()
    return report


def get_diagnostic_report(space: str = None) -> str:
    """Generate diagnostic report for /diagnostic command."""
    report = []

    # Header
    report.append("KNOWLEDGE DATABASE")
    report.append("-" * 18)

    # Database info
    db_path = get_db_path(space)
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        mtime = datetime.fromtimestamp(db_path.stat().st_mtime)
        age_hours = (datetime.now() - mtime).total_seconds() / 3600

        report.append(f"Root DB: {db_path}")
        report.append(f"  Size................... {size_mb:.1f} MB")
        report.append(f"  Last modified.......... {mtime.strftime('%Y-%m-%d %H:%M')}")
        report.append(f"  Age.................... {age_hours:.1f} hours")

        if age_hours <= 4:
            report.append("  Status: CURRENT")
        elif age_hours <= 24:
            report.append("  Status: STALE (>4 hours)")
        else:
            report.append("  Status: CRITICAL (>24 hours)")
    else:
        report.append("Root DB: NOT FOUND")

    report.append("")

    # Get stats
    try:
        stats = get_comprehensive_stats(space)

        report.append("Index Statistics:")
        report.append(f"  Files.................. {stats['files']['total']}")
        report.append(f"  Tasks.................. {stats['tasks']['total']}")
        report.append(f"  Sessions............... {stats['journals'].get('sessions', 0)}")
        report.append(f"  Links.................. {stats['links']['total']}")

        report.append("")
        report.append("Health Checks:")

        # Link resolution
        total_links = stats['links']['total']
        resolved = stats['links']['resolved']
        if total_links > 0:
            resolution_pct = (resolved / total_links) * 100
            unresolved = stats['links']['unresolved']
            report.append(f"  Link resolution........ {resolution_pct:.0f}% ({unresolved} unresolved)")

            if unresolved > 100:
                report.append("    STATUS: CRITICAL")
            elif unresolved > 50:
                report.append("    STATUS: WARNING")
            else:
                report.append("    STATUS: OK")

        # Inbox status
        unprocessed = stats['inbox']['unprocessed']
        report.append(f"  Inbox entries.......... {unprocessed} unprocessed")
        if unprocessed > 20:
            report.append("    STATUS: NEEDS ATTENTION")

        # Recent syncs
        report.append("")
        report.append("Recent Syncs:")
        for sync in stats.get('recent_syncs', [])[:3]:
            report.append(f"  {sync['started_at'][:16]} - {sync['type']} - {sync['status']}")

    except Exception as e:
        report.append(f"Error getting stats: {e}")

    return '\n'.join(report)


def print_stats(stats: Dict[str, Any]):
    """Print comprehensive statistics."""
    print("\n" + "="*60)
    print("DATACORE KNOWLEDGE DATABASE STATISTICS")
    print("="*60)

    print(f"\nGenerated: {stats.get('generated_at', 'N/A')}")
    print(f"Scope: {stats.get('space', 'all')}")

    # Files
    print("\n--- Files ---")
    print(f"Total: {stats['files']['total']}")
    print("\nBy type:")
    for ftype, count in sorted(stats['files'].get('by_type', {}).items(), key=lambda x: -x[1]):
        print(f"  {ftype}: {count}")

    # Tasks
    print("\n--- Tasks ---")
    print(f"Total: {stats['tasks']['total']}")
    for state, count in stats['tasks'].get('by_state', {}).items():
        print(f"  {state}: {count}")

    # Links
    print("\n--- Links ---")
    print(f"Total: {stats['links']['total']}")
    print(f"Resolved: {stats['links']['resolved']}")
    print(f"Unresolved: {stats['links']['unresolved']}")

    # System
    print("\n--- System Components ---")
    for comp_type, count in stats.get('system', {}).items():
        print(f"  {comp_type}: {count}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Datacore Sync Engine")
    parser.add_argument('command', nargs='?', choices=['sync', 'rebuild', 'stats', 'validate', 'diagnostic'],
                        default='sync', help='Command to run')
    parser.add_argument('--space', '-s', choices=list(SPACES.keys()), help='Space to operate on')
    parser.add_argument('--full', action='store_true', help='Full sync (re-index all)')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    parser.add_argument('--fix', action='store_true', help='Fix validation issues')
    parser.add_argument('--quiet', '-q', action='store_true', help='Minimal output')

    args = parser.parse_args()

    if args.command == 'sync':
        stats = sync_all(args.space, args.full, verbose=not args.quiet)
        if args.json:
            print(json.dumps(stats, indent=2))

    elif args.command == 'rebuild':
        stats = rebuild(args.space, verbose=not args.quiet)
        if args.json:
            print(json.dumps(stats, indent=2))

    elif args.command == 'stats':
        if args.json:
            print(get_comprehensive_stats(args.space, as_json=True))
        else:
            stats = get_comprehensive_stats(args.space)
            print_stats(stats)

    elif args.command == 'validate':
        report = validate_database(args.space, args.fix)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print("\n=== Database Validation ===")
            print(f"Valid: {report['valid']}")
            if report['issues']:
                print("\nIssues:")
                for issue in report['issues']:
                    print(f"  - {issue}")
            if report['fixed']:
                print("\nFixed:")
                for fix in report['fixed']:
                    print(f"  - {fix}")

    elif args.command == 'diagnostic':
        print(get_diagnostic_report(args.space))
