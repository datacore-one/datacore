#!/usr/bin/env python3
"""
Query Library (DIP-0004)

Convenience functions for common database queries used by agents.
Provides clean Python APIs for accessing indexed content.

Categories:
- Tasks: Actionable items, AI-delegated tasks, by tag/state
- Sessions: Recent work, by type
- System: Agents, commands, DIPs, specs
- Learning: Patterns, corrections, preferences
- Search: Full-text search across content

Usage:
    from query_library import (
        get_ai_tasks,
        get_actionable_tasks,
        search_content,
        get_recent_sessions,
    )

    tasks = get_ai_tasks(space='personal')
    results = search_content('bidirectional sync')
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

# Add lib to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from zettel_db import get_connection, SPACES


# =============================================================================
# TASK QUERIES
# =============================================================================

def get_ai_tasks(space: str = None, status: str = 'TODO') -> List[Dict[str, Any]]:
    """Get tasks tagged for AI processing.

    Returns tasks with :AI: tag variants.
    """
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT t.*, p.name as project_name
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        WHERE t.tags LIKE '%:AI:%'
        AND t.state = ?
        ORDER BY t.priority, t.created_at
    """, (status,))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_tasks_by_tag(tag: str, space: str = None, include_done: bool = False) -> List[Dict[str, Any]]:
    """Get tasks with a specific tag."""
    conn = get_connection(space)
    cursor = conn.cursor()

    where_clause = "WHERE t.tags LIKE ?"
    if not include_done:
        where_clause += " AND t.state NOT IN ('DONE', 'CANCELLED')"

    cursor.execute(f"""
        SELECT t.*, p.name as project_name
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        {where_clause}
        ORDER BY t.priority, t.created_at
    """, (f'%:{tag}:%',))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_actionable_tasks(space: str = None, limit: int = 20) -> List[Dict[str, Any]]:
    """Get tasks ready for action (NEXT or TODO without blockers)."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT t.*, p.name as project_name
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        WHERE t.state IN ('NEXT', 'TODO')
        ORDER BY
            CASE t.state WHEN 'NEXT' THEN 0 ELSE 1 END,
            t.priority,
            t.scheduled,
            t.created_at
        LIMIT ?
    """, (limit,))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_waiting_tasks(space: str = None) -> List[Dict[str, Any]]:
    """Get tasks in WAITING state."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT t.*, p.name as project_name
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        WHERE t.state = 'WAITING'
        ORDER BY t.scheduled, t.created_at
    """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_scheduled_tasks(
    date_from: str = None,
    date_to: str = None,
    space: str = None
) -> List[Dict[str, Any]]:
    """Get tasks scheduled within a date range."""
    conn = get_connection(space)
    cursor = conn.cursor()

    where_clause = "WHERE t.scheduled IS NOT NULL"
    params = []

    if date_from:
        where_clause += " AND t.scheduled >= ?"
        params.append(date_from)
    if date_to:
        where_clause += " AND t.scheduled <= ?"
        params.append(date_to)

    cursor.execute(f"""
        SELECT t.*, p.name as project_name
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        {where_clause}
        AND t.state NOT IN ('DONE', 'CANCELLED')
        ORDER BY t.scheduled, t.priority
    """, params)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_overdue_tasks(space: str = None) -> List[Dict[str, Any]]:
    """Get tasks past their scheduled date."""
    today = datetime.now().strftime('%Y-%m-%d')
    return get_scheduled_tasks(date_to=today, space=space)


def get_task_stats(space: str = None) -> Dict[str, Any]:
    """Get aggregate task statistics."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            state,
            COUNT(*) as count
        FROM tasks
        GROUP BY state
    """)

    by_state = {row['state']: row['count'] for row in cursor.fetchall()}

    cursor.execute("""
        SELECT COUNT(*) as count FROM tasks WHERE tags LIKE '%:AI:%'
    """)
    ai_count = cursor.fetchone()['count']

    cursor.execute("""
        SELECT COUNT(*) as count FROM tasks
        WHERE scheduled IS NOT NULL
        AND scheduled < date('now')
        AND state NOT IN ('DONE', 'CANCELLED')
    """)
    overdue_count = cursor.fetchone()['count']

    conn.close()

    return {
        'by_state': by_state,
        'total': sum(by_state.values()),
        'ai_delegated': ai_count,
        'overdue': overdue_count,
    }


# =============================================================================
# PROJECT QUERIES
# =============================================================================

def get_active_projects(space: str = None) -> List[Dict[str, Any]]:
    """Get active projects with task counts."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            p.*,
            COUNT(t.id) as total_tasks,
            SUM(CASE WHEN t.state = 'DONE' THEN 1 ELSE 0 END) as done_tasks
        FROM projects p
        LEFT JOIN tasks t ON t.project_id = p.id
        WHERE p.status = 'active'
        GROUP BY p.id
        ORDER BY p.name
    """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_project_tasks(project_id: int, space: str = None) -> List[Dict[str, Any]]:
    """Get all tasks for a project."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM tasks
        WHERE project_id = ?
        ORDER BY state, priority, created_at
    """, (project_id,))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


# =============================================================================
# SESSION QUERIES
# =============================================================================

def get_recent_sessions(days: int = 7, space: str = None) -> List[Dict[str, Any]]:
    """Get sessions from the last N days."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.*, j.date
        FROM sessions s
        JOIN journal_entries j ON s.journal_id = j.id
        WHERE j.date >= date('now', ?)
        ORDER BY j.date DESC, s.id DESC
    """, (f'-{days} days',))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_sessions_by_type(
    session_type: str,
    days: int = 30,
    space: str = None
) -> List[Dict[str, Any]]:
    """Get sessions of a specific type."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.*, j.date
        FROM sessions s
        JOIN journal_entries j ON s.journal_id = j.id
        WHERE s.session_type = ?
        AND j.date >= date('now', ?)
        ORDER BY j.date DESC
    """, (session_type, f'-{days} days'))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_accomplishments(days: int = 7, space: str = None) -> List[Dict[str, Any]]:
    """Get accomplishments from recent sessions."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT a.description, s.title as session_title, j.date
        FROM accomplishments a
        JOIN sessions s ON a.session_id = s.id
        JOIN journal_entries j ON s.journal_id = j.id
        WHERE j.date >= date('now', ?)
        ORDER BY j.date DESC, s.id, a.id
    """, (f'-{days} days',))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


# =============================================================================
# SYSTEM QUERIES
# =============================================================================

def get_agents(space: str = None) -> List[Dict[str, Any]]:
    """Get all registered agents."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM system_components
        WHERE type = 'agent'
        ORDER BY name
    """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_commands(space: str = None) -> List[Dict[str, Any]]:
    """Get all registered commands."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM system_components
        WHERE type = 'command'
        ORDER BY name
    """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_dips(space: str = None) -> List[Dict[str, Any]]:
    """Get all DIPs (Datacore Improvement Proposals)."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM dips
        ORDER BY dip_number
    """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_specs(space: str = None) -> List[Dict[str, Any]]:
    """Get all specs."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM specs
        ORDER BY name
    """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


# =============================================================================
# LEARNING QUERIES
# =============================================================================

def get_patterns(category: str = None, space: str = None) -> List[Dict[str, Any]]:
    """Get learning patterns, optionally filtered by category."""
    conn = get_connection(space)
    cursor = conn.cursor()

    if category:
        cursor.execute("""
            SELECT * FROM learning_entries
            WHERE type = 'pattern' AND category = ?
            ORDER BY created_at DESC
        """, (category,))
    else:
        cursor.execute("""
            SELECT * FROM learning_entries
            WHERE type = 'pattern'
            ORDER BY created_at DESC
        """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_corrections(space: str = None) -> List[Dict[str, Any]]:
    """Get learning corrections (mistakes to avoid)."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM learning_entries
        WHERE type = 'correction'
        ORDER BY created_at DESC
    """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_preferences(space: str = None) -> List[Dict[str, Any]]:
    """Get user preferences."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM learning_entries
        WHERE type = 'preference'
        ORDER BY category, created_at DESC
    """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


# =============================================================================
# SEARCH QUERIES
# =============================================================================

def search_content(
    query: str,
    content_type: str = None,
    space: str = None,
    limit: int = 20
) -> List[Dict[str, Any]]:
    """Full-text search across indexed content.

    Args:
        query: Search query (supports FTS5 syntax)
        content_type: Filter by type (zettel, file, task, etc.)
        space: Filter by space
        limit: Maximum results

    Returns:
        List of matches with snippets
    """
    conn = get_connection(space)
    cursor = conn.cursor()

    # Search files FTS
    results = []

    try:
        cursor.execute("""
            SELECT
                f.id,
                'file' as result_type,
                f.title,
                f.path,
                snippet(files_fts, 0, '<mark>', '</mark>', '...', 32) as snippet,
                bm25(files_fts) as rank
            FROM files_fts
            JOIN files f ON files_fts.rowid = f.id
            WHERE files_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, limit))

        for row in cursor.fetchall():
            results.append(dict(row))
    except Exception:
        pass  # FTS table may not exist yet

    # Search tasks
    try:
        cursor.execute("""
            SELECT
                id,
                'task' as result_type,
                heading as title,
                source_file as path,
                heading as snippet,
                0 as rank
            FROM tasks
            WHERE heading LIKE ?
            LIMIT ?
        """, (f'%{query}%', limit))

        for row in cursor.fetchall():
            results.append(dict(row))
    except Exception:
        pass

    # Sort by rank and limit
    results.sort(key=lambda x: x.get('rank', 0))
    conn.close()

    return results[:limit]


def search_tasks(
    query: str,
    space: str = None,
    include_done: bool = False
) -> List[Dict[str, Any]]:
    """Search tasks by heading content."""
    conn = get_connection(space)
    cursor = conn.cursor()

    where_clause = "WHERE t.heading LIKE ?"
    if not include_done:
        where_clause += " AND t.state NOT IN ('DONE', 'CANCELLED')"

    cursor.execute(f"""
        SELECT t.*, p.name as project_name
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        {where_clause}
        ORDER BY t.priority, t.created_at
    """, (f'%{query}%',))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


# =============================================================================
# LINK QUERIES
# =============================================================================

def get_backlinks(target_path: str, space: str = None) -> List[Dict[str, Any]]:
    """Get all files that link to a given file."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            f.path as source_path,
            f.title as source_title,
            l.link_type,
            l.context
        FROM links l
        JOIN files f ON l.source_id = f.id
        WHERE l.target_path = ?
        ORDER BY f.title
    """, (target_path,))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_outgoing_links(source_path: str, space: str = None) -> List[Dict[str, Any]]:
    """Get all links from a file."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            l.target_path,
            l.link_type,
            l.resolved,
            f2.title as target_title
        FROM links l
        JOIN files f1 ON l.source_id = f1.id
        LEFT JOIN files f2 ON l.target_id = f2.id
        WHERE f1.path = ?
        ORDER BY l.target_path
    """, (source_path,))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_unresolved_links(space: str = None) -> List[Dict[str, Any]]:
    """Get links that point to non-existent files."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            l.target_path,
            COUNT(*) as reference_count,
            GROUP_CONCAT(f.title, ', ') as referencing_files
        FROM links l
        JOIN files f ON l.source_id = f.id
        WHERE l.resolved = 0
        GROUP BY l.target_path
        ORDER BY reference_count DESC
    """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


# =============================================================================
# TRADING QUERIES (Module-specific)
# =============================================================================

def get_trading_entries(
    days: int = 30,
    space: str = None
) -> List[Dict[str, Any]]:
    """Get trading journal entries."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT te.*, j.date
        FROM trading_entries te
        JOIN journal_entries j ON te.journal_id = j.id
        WHERE j.date >= date('now', ?)
        ORDER BY j.date DESC
    """, (f'-{days} days',))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_trading_stats(days: int = 30, space: str = None) -> Dict[str, Any]:
    """Get trading statistics."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            COUNT(*) as entry_count,
            AVG(emotional_state) as avg_emotional_state,
            SUM(pnl_realized) as total_pnl,
            AVG(imr) as avg_imr,
            AVG(phs) as avg_phs
        FROM trading_entries te
        JOIN journal_entries j ON te.journal_id = j.id
        WHERE j.date >= date('now', ?)
    """, (f'-{days} days',))

    row = cursor.fetchone()
    conn.close()

    return {
        'entry_count': row['entry_count'] or 0,
        'avg_emotional_state': round(row['avg_emotional_state'] or 0, 1),
        'total_pnl': row['total_pnl'] or 0,
        'avg_imr': round(row['avg_imr'] or 0, 1),
        'avg_phs': round(row['avg_phs'] or 0, 1),
    }


# =============================================================================
# DATABASE HEALTH
# =============================================================================

def get_database_stats(space: str = None) -> Dict[str, Any]:
    """Get database statistics for health monitoring."""
    conn = get_connection(space)
    cursor = conn.cursor()

    stats = {}

    # Count tables
    tables = [
        'files', 'tasks', 'projects', 'journal_entries',
        'sessions', 'system_components', 'dips', 'learning_entries',
        'links', 'pending_writes'
    ]

    for table in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) as count FROM {table}")
            stats[table] = cursor.fetchone()['count']
        except Exception:
            stats[table] = 0

    # Last sync time
    cursor.execute("""
        SELECT MAX(indexed_at) as last_sync FROM file_checksums
    """)
    row = cursor.fetchone()
    stats['last_sync'] = row['last_sync'] if row else None

    conn.close()
    return stats


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Query Library CLI")
    parser.add_argument('query_type', choices=[
        'ai-tasks', 'actionable', 'waiting', 'overdue', 'task-stats',
        'sessions', 'accomplishments',
        'agents', 'commands', 'dips',
        'patterns', 'corrections',
        'search', 'db-stats'
    ])
    parser.add_argument('--space', '-s', choices=list(SPACES.keys()))
    parser.add_argument('--query', '-q', help='Search query')
    parser.add_argument('--days', '-d', type=int, default=7)
    parser.add_argument('--json', action='store_true', help='Output as JSON')

    args = parser.parse_args()

    result = None

    if args.query_type == 'ai-tasks':
        result = get_ai_tasks(args.space)
    elif args.query_type == 'actionable':
        result = get_actionable_tasks(args.space)
    elif args.query_type == 'waiting':
        result = get_waiting_tasks(args.space)
    elif args.query_type == 'overdue':
        result = get_overdue_tasks(args.space)
    elif args.query_type == 'task-stats':
        result = get_task_stats(args.space)
    elif args.query_type == 'sessions':
        result = get_recent_sessions(args.days, args.space)
    elif args.query_type == 'accomplishments':
        result = get_accomplishments(args.days, args.space)
    elif args.query_type == 'agents':
        result = get_agents(args.space)
    elif args.query_type == 'commands':
        result = get_commands(args.space)
    elif args.query_type == 'dips':
        result = get_dips(args.space)
    elif args.query_type == 'patterns':
        result = get_patterns(space=args.space)
    elif args.query_type == 'corrections':
        result = get_corrections(args.space)
    elif args.query_type == 'search':
        if not args.query:
            print("--query required for search")
            sys.exit(1)
        result = search_content(args.query, space=args.space)
    elif args.query_type == 'db-stats':
        result = get_database_stats(args.space)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if isinstance(result, list):
            for item in result:
                if 'heading' in item:
                    print(f"- [{item.get('state', '?')}] {item['heading']}")
                elif 'title' in item:
                    print(f"- {item['title']}")
                elif 'description' in item:
                    print(f"- {item['description']}")
                elif 'name' in item:
                    print(f"- {item['name']}: {item.get('description', '')[:60]}")
                else:
                    print(f"- {item}")
        elif isinstance(result, dict):
            for key, value in result.items():
                print(f"{key}: {value}")
