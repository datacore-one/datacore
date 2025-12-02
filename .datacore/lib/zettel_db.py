#!/usr/bin/env python3
"""
Knowledge Database Manager (DIP-0004)

Unified database for ALL Datacore content:
- Markdown files (zettels, pages, journals, notes)
- Org-mode files (tasks, projects, inbox, habits)
- System components (agents, commands, specs, DIPs)
- Learning entries (patterns, corrections, preferences)

Core Principle: Markdown/org files are source of truth. DB is derived index.

Database hierarchy:
- Root DB: ~/Data/.datacore/knowledge.db (all spaces, cross-space queries)
- Space DBs: ~/Data/{space}/.datacore/knowledge.db (space-specific)

Usage:
    python zettel_db.py init [--space SPACE]
    python zettel_db.py rebuild [--space SPACE]
    python zettel_db.py sync [--space SPACE] [--full]
    python zettel_db.py stats [--space SPACE] [--json]
    python zettel_db.py search <query> [--space SPACE] [--type TYPE]
    python zettel_db.py unresolved [--space SPACE]
    python zettel_db.py orphans [--space SPACE]
    python zettel_db.py validate [--fix]
"""

import sqlite3
import os
import json
import hashlib
from pathlib import Path
from datetime import datetime, date

# Base paths
DATA_ROOT = Path.home() / "Data"
ROOT_DB_PATH = DATA_ROOT / ".datacore" / "knowledge.db"

# Space configurations - ALL content locations
SPACES = {
    'personal': {
        'path': DATA_ROOT / '0-personal',
        'scan_paths': [
            # Knowledge
            DATA_ROOT / '0-personal' / 'notes' / '2-knowledge' / 'zettel',
            DATA_ROOT / '0-personal' / 'notes' / 'pages',
            DATA_ROOT / '0-personal' / 'notes' / 'Clippings',
            DATA_ROOT / '0-personal' / 'notes' / '0-inbox',
            DATA_ROOT / '0-personal' / 'notes' / '1-active',
            # Journals
            DATA_ROOT / '0-personal' / 'notes' / 'journals',
        ],
        'org_paths': [
            DATA_ROOT / '0-personal' / 'org',
        ],
        'journal_path': DATA_ROOT / '0-personal' / 'notes' / 'journals',
    },
    'datafund': {
        'path': DATA_ROOT / '1-datafund',
        'scan_paths': [
            DATA_ROOT / '1-datafund' / '3-knowledge' / 'zettel',
            DATA_ROOT / '1-datafund' / '3-knowledge' / 'pages',
            DATA_ROOT / '1-datafund' / '3-knowledge' / 'literature',
            DATA_ROOT / '1-datafund' / '1-tracks' / 'research',
        ],
        'org_paths': [
            DATA_ROOT / '1-datafund' / 'org',
        ],
        'journal_path': DATA_ROOT / '1-datafund' / 'journal',
    },
    'datacore': {
        'path': DATA_ROOT / '2-datacore',
        'scan_paths': [
            DATA_ROOT / '2-datacore' / '3-knowledge' / 'zettel',
            DATA_ROOT / '2-datacore' / '3-knowledge' / 'pages',
        ],
        'org_paths': [
            DATA_ROOT / '2-datacore' / 'org',
        ],
        'journal_path': DATA_ROOT / '2-datacore' / 'journal',
    },
}

# System paths (core datacore)
SYSTEM_PATHS = {
    'agents': DATA_ROOT / '.datacore' / 'agents',
    'commands': DATA_ROOT / '.datacore' / 'commands',
    'specs': DATA_ROOT / '.datacore' / 'specs',
    'dips': DATA_ROOT / '.datacore' / 'dips',
    'learning': DATA_ROOT / '.datacore' / 'learning',
    'modules': DATA_ROOT / '.datacore' / 'modules',
}

# File type detection based on path and filename
def detect_file_type(path):
    """Detect file type based on path location and filename.

    Types:
    - System: claude, scaffolding, readme, config
    - Specs: agent, command, workflow, spec
    - Knowledge: zettel, page, literature, research
    - Temporal: journal, inbox
    - Content: clipping, report, active
    - Other: note
    """
    path_str = str(path)
    path_lower = path_str.lower()
    filename = Path(path).name
    filename_lower = filename.lower()

    # System files (by filename)
    if filename == 'CLAUDE.md':
        return 'claude'
    elif filename == 'SCAFFOLDING.md':
        return 'scaffolding'
    elif filename_lower == 'readme.md':
        return 'readme'
    elif filename_lower.endswith('-spec.md') or filename_lower.endswith('_spec.md'):
        return 'spec'

    # Spec files (by path)
    if '/agents/' in path_lower or '/.datacore/agents/' in path_lower:
        return 'agent'
    elif '/commands/' in path_lower or '/.datacore/commands/' in path_lower:
        return 'command'
    elif '/workflows/' in path_lower:
        return 'workflow'

    # Knowledge files
    if '/zettel/' in path_lower:
        return 'zettel'
    elif '/pages/' in path_lower:
        return 'page'
    elif '/literature/' in path_lower:
        return 'literature'
    elif '/research/' in path_lower:
        return 'research'

    # Temporal files
    if '/journals/' in path_lower:
        return 'journal'
    elif '/inbox/' in path_lower or '/0-inbox/' in path_lower:
        return 'inbox'

    # Content files
    if '/clippings/' in path_lower:
        return 'clipping'
    elif '/reports/' in path_lower:
        return 'report'
    elif '/active/' in path_lower or '/1-active/' in path_lower:
        return 'active'

    # Architecture/config files
    if '/.datacore/' in path_str:
        return 'config'
    elif '/departments/' in path_lower:
        return 'department'

    return 'note'


def detect_author(content, frontmatter, path):
    """Detect if file was created by human or AI.

    Returns: 'human', 'ai', 'ai-assisted', or 'unknown'
    """
    path_str = str(path).lower()
    content_lower = content.lower() if content else ''

    # Check frontmatter
    if frontmatter:
        author_field = frontmatter.get('author', '')
        # Handle list format (e.g., author: [Name1, Name2])
        if isinstance(author_field, list):
            author_field = ' '.join(str(a) for a in author_field)
        author = str(author_field).lower() if author_field else ''

        if author in ['ai', 'claude', 'gpt', 'chatgpt'] or 'claude' in author or 'gpt' in author:
            return 'ai'
        elif author in ['human', 'gregor'] or 'gregor' in author:
            return 'human'

        # Check for AI generation markers
        if frontmatter.get('status') == 'stub':
            return 'ai'

        tags = frontmatter.get('tags', [])
        if isinstance(tags, list):
            tag_list = [str(t).lower() for t in tags]
            if 'ai-generated' in tag_list or 'stub' in tag_list:
                return 'ai'

    # Check content markers
    if 'auto-generated stub' in content_lower:
        return 'ai'
    if 'generated with claude' in content_lower:
        return 'ai'
    if 'co-authored-by: claude' in content_lower:
        return 'ai-assisted'

    # Check path patterns
    if '/reports/' in path_str:
        return 'ai'  # Reports are typically AI-generated

    # Stubs are AI-generated
    if frontmatter and frontmatter.get('status') == 'stub':
        return 'ai'

    return 'unknown'


def get_db_path(space=None):
    """Get database path for a space or root."""
    if space is None:
        return ROOT_DB_PATH
    if space not in SPACES:
        raise ValueError(f"Unknown space: {space}. Valid: {list(SPACES.keys())}")
    return SPACES[space]['path'] / '.datacore' / 'knowledge.db'


def get_connection(space=None):
    """Get database connection with row factory."""
    db_path = get_db_path(space)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_database(space=None):
    """Initialize the database schema."""
    conn = get_connection(space)
    cursor = conn.cursor()

    # Core files table - ALL markdown files
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY,
            path TEXT UNIQUE NOT NULL,
            space TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'note',
            title TEXT NOT NULL,
            content TEXT,
            summary TEXT,
            word_count INTEGER,
            maturity TEXT DEFAULT 'seedling',
            is_stub BOOLEAN DEFAULT 0,
            author TEXT DEFAULT 'unknown',
            created_at TEXT,
            updated_at TEXT,
            processed_at TEXT
        )
    """)

    # Add author column if missing (migration for existing DBs)
    try:
        cursor.execute("ALTER TABLE files ADD COLUMN author TEXT DEFAULT 'unknown'")
    except sqlite3.OperationalError:
        pass  # Column already exists

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_author ON files(author)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_space ON files(space)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_type ON files(type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_title ON files(title)")

    # Extracted terms for similarity matching
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS terms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL,
            term TEXT NOT NULL,
            frequency INTEGER DEFAULT 1,
            is_entity BOOLEAN DEFAULT 0,
            entity_type TEXT,
            FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_terms_term ON terms(term)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_terms_file ON terms(file_id)")

    # Links/References between files (many-to-many - every reference is a row)
    # Roam-style: [[link]], #tag, #[[tag]] are all page references
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            target_id TEXT,
            target_title TEXT NOT NULL,
            link_type TEXT DEFAULT 'related',
            syntax TEXT DEFAULT 'wiki-link',
            resolved BOOLEAN DEFAULT 0,
            created_at TEXT,
            FOREIGN KEY (source_id) REFERENCES files(id) ON DELETE CASCADE,
            FOREIGN KEY (target_id) REFERENCES files(id) ON DELETE SET NULL
        )
    """)

    # Add syntax column if missing (migration for existing DBs)
    try:
        cursor.execute("ALTER TABLE links ADD COLUMN syntax TEXT DEFAULT 'wiki-link'")
    except sqlite3.OperationalError:
        pass  # Column already exists

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_links_title ON links(target_title)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_links_syntax ON links(syntax)")

    # Tags table for tag analysis and normalization
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL,
            tag TEXT NOT NULL,
            normalized_tag TEXT,
            FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_normalized ON tags(normalized_tag)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_file ON tags(file_id)")

    # Topic clusters (for future topic-weaver)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS topics (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            file_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'emergent',
            created_at TEXT,
            updated_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS topic_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id TEXT NOT NULL,
            file_id TEXT NOT NULL,
            relevance_score REAL DEFAULT 0.5,
            FOREIGN KEY (topic_id) REFERENCES topics(id) ON DELETE CASCADE,
            FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
            UNIQUE(topic_id, file_id)
        )
    """)

    # =========================================================================
    # GTD TABLES (DIP-0004 Phase 1)
    # =========================================================================

    # Org-mode TODO items
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT REFERENCES files(id) ON DELETE CASCADE,
            org_id TEXT,
            state TEXT NOT NULL,
            heading TEXT NOT NULL,
            level INTEGER,
            priority TEXT,
            scheduled TEXT,
            deadline TEXT,
            closed_at TEXT,
            category TEXT,
            effort INTEGER,
            tags TEXT,
            properties TEXT,
            parent_id INTEGER REFERENCES tasks(id),
            project_id INTEGER REFERENCES projects(id),
            space TEXT,
            source_file TEXT NOT NULL,
            line_number INTEGER,
            checksum TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_category ON tasks(category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_space ON tasks(space)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_scheduled ON tasks(scheduled)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_tags ON tasks(tags)")

    # PROJECT entries
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT REFERENCES files(id),
            org_id TEXT,
            name TEXT NOT NULL,
            status TEXT,
            category TEXT,
            outcome TEXT,
            next_action_id INTEGER REFERENCES tasks(id),
            oldest_task_date TEXT,
            space TEXT,
            source_file TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_space ON projects(space)")

    # Inbox entries (before processing)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inbox_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            source TEXT,
            raw_content TEXT,
            processed INTEGER DEFAULT 0,
            processed_at TEXT,
            routed_to TEXT,
            routed_task_id INTEGER REFERENCES tasks(id),
            space TEXT,
            source_file TEXT,
            line_number INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_inbox_processed ON inbox_entries(processed)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_inbox_space ON inbox_entries(space)")

    # Habit tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT REFERENCES files(id),
            name TEXT NOT NULL,
            frequency TEXT,
            scheduled_days TEXT,
            last_completion TEXT,
            streak INTEGER DEFAULT 0,
            total_completions INTEGER DEFAULT 0,
            space TEXT,
            source_file TEXT,
            created_at TEXT
        )
    """)

    # =========================================================================
    # JOURNAL TABLES (DIP-0004 Phase 2)
    # =========================================================================

    # Daily journal entries
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT REFERENCES files(id),
            date TEXT NOT NULL,
            space TEXT,
            type TEXT,
            content TEXT,
            word_count INTEGER,
            session_count INTEGER,
            source_file TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_date ON journal_entries(date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_space ON journal_entries(space)")

    # Work sessions within journals
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journal_id INTEGER REFERENCES journal_entries(id) ON DELETE CASCADE,
            title TEXT,
            goal TEXT,
            started_at TEXT,
            ended_at TEXT,
            duration_minutes INTEGER,
            space TEXT,
            session_type TEXT,
            content TEXT,
            created_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_journal ON sessions(journal_id)")

    # Accomplishments per session
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accomplishments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
            description TEXT NOT NULL,
            category TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Files modified per session
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS files_modified (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
            file_path TEXT NOT NULL,
            change_type TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Decisions captured
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER REFERENCES sessions(id),
            file_id TEXT REFERENCES files(id),
            description TEXT NOT NULL,
            rationale TEXT,
            reversible INTEGER DEFAULT 1,
            affects TEXT,
            tags TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Trading journal entries
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trading_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journal_id INTEGER REFERENCES journal_entries(id),
            date TEXT NOT NULL,
            session_type TEXT,
            emotional_state INTEGER,
            emotional_notes TEXT,
            framework_violations TEXT,
            position_changes TEXT,
            pnl_realized REAL,
            pnl_unrealized REAL,
            imr REAL,
            phs INTEGER,
            notes TEXT,
            created_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trading_date ON trading_entries(date)")

    # =========================================================================
    # SYSTEM TABLES (DIP-0004 Phase 3)
    # =========================================================================

    # Agents, commands, modules, workflows
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_components (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            path TEXT NOT NULL,
            module TEXT,
            provides TEXT,
            dependencies TEXT,
            triggers TEXT,
            when_to_use TEXT,
            space TEXT,
            version TEXT,
            source_file TEXT NOT NULL,
            checksum TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(type, name, space)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_components_type ON system_components(type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_components_name ON system_components(name)")

    # Datacore Improvement Proposals
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number INTEGER UNIQUE NOT NULL,
            title TEXT NOT NULL,
            status TEXT,
            abstract TEXT,
            affects TEXT,
            related_specs TEXT,
            related_dips TEXT,
            author TEXT,
            source_file TEXT NOT NULL,
            checksum TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_dips_status ON dips(status)")

    # Specifications
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS specs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT,
            version TEXT,
            content TEXT,
            related_dips TEXT,
            source_file TEXT NOT NULL,
            checksum TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    # Learning entries (patterns, corrections, preferences)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS learning_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            title TEXT,
            content TEXT NOT NULL,
            source_session INTEGER REFERENCES sessions(id),
            source_file TEXT,
            tags TEXT,
            applies_to TEXT,
            priority INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_learning_type ON learning_entries(type)")

    # Scaffolding requirements (DIP-0003)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scaffolding_requirements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            space TEXT NOT NULL,
            category TEXT NOT NULL,
            document TEXT NOT NULL,
            status TEXT,
            file_path TEXT,
            coverage_score REAL,
            last_checked TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scaffolding_space ON scaffolding_requirements(space)")

    # Context metadata (CLAUDE.md sync state)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS context_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            space TEXT,
            file_path TEXT NOT NULL,
            line_count INTEGER,
            agent_count INTEGER,
            command_count INTEGER,
            module_count INTEGER,
            checksum TEXT,
            verified_date TEXT,
            sync_status TEXT,
            issues TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    # =========================================================================
    # SYNC TRACKING TABLES (DIP-0004 Phase 4)
    # =========================================================================

    # Track pending write-backs to source files
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_writes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            operation TEXT NOT NULL,
            changes TEXT,
            target_file TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            error_message TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            applied_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_writes(status)")

    # File change detection
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS file_checksums (
            path TEXT PRIMARY KEY,
            checksum TEXT NOT NULL,
            indexed_at TEXT NOT NULL,
            modified_at TEXT
        )
    """)

    # Sync history
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sync_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_type TEXT,
            started_at TEXT,
            completed_at TEXT,
            files_scanned INTEGER,
            files_updated INTEGER,
            writes_applied INTEGER,
            errors TEXT,
            status TEXT
        )
    """)

    # Full-text search virtual table
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
            title,
            content,
            summary,
            content='files',
            content_rowid='rowid'
        )
    """)

    # Triggers to keep FTS in sync
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
            INSERT INTO files_fts(rowid, title, content, summary)
            VALUES (NEW.rowid, NEW.title, NEW.content, NEW.summary);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
            INSERT INTO files_fts(files_fts, rowid, title, content, summary)
            VALUES('delete', OLD.rowid, OLD.title, OLD.content, OLD.summary);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
            INSERT INTO files_fts(files_fts, rowid, title, content, summary)
            VALUES('delete', OLD.rowid, OLD.title, OLD.content, OLD.summary);
            INSERT INTO files_fts(rowid, title, content, summary)
            VALUES (NEW.rowid, NEW.title, NEW.content, NEW.summary);
        END
    """)

    conn.commit()
    conn.close()

    db_path = get_db_path(space)
    space_label = space if space else "root"
    print(f"Database initialized: {db_path} ({space_label})")


def init_all_databases():
    """Initialize root DB and all space DBs."""
    init_database(None)  # Root
    for space in SPACES:
        init_database(space)


def get_stats(space=None, file_type=None):
    """Get database statistics."""
    conn = get_connection(space)
    cursor = conn.cursor()

    stats = {}

    # Total files
    if file_type:
        cursor.execute("SELECT COUNT(*) FROM files WHERE type = ?", (file_type,))
    else:
        cursor.execute("SELECT COUNT(*) FROM files")
    stats['total_files'] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM files WHERE is_stub = 1")
    stats['stubs'] = cursor.fetchone()[0]

    # By space
    cursor.execute("SELECT space, COUNT(*) FROM files GROUP BY space ORDER BY COUNT(*) DESC")
    stats['by_space'] = {row['space']: row[1] for row in cursor.fetchall()}

    # By type
    cursor.execute("SELECT type, COUNT(*) FROM files GROUP BY type ORDER BY COUNT(*) DESC")
    stats['by_type'] = {row['type']: row[1] for row in cursor.fetchall()}

    # Links/References
    cursor.execute("SELECT COUNT(*) FROM links")
    stats['total_links'] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM links WHERE resolved = 1")
    stats['resolved_links'] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT target_title) FROM links WHERE resolved = 0")
    stats['unresolved_targets'] = cursor.fetchone()[0]

    # Reference syntax breakdown
    cursor.execute("SELECT syntax, COUNT(*) FROM links GROUP BY syntax ORDER BY COUNT(*) DESC")
    stats['by_syntax'] = {row['syntax'] or 'wiki-link': row[1] for row in cursor.fetchall()}

    # Maturity
    cursor.execute("SELECT maturity, COUNT(*) FROM files WHERE type = 'zettel' GROUP BY maturity")
    stats['by_maturity'] = {row['maturity']: row[1] for row in cursor.fetchall()}

    # By author
    cursor.execute("SELECT author, COUNT(*) FROM files GROUP BY author ORDER BY COUNT(*) DESC")
    stats['by_author'] = {row['author']: row[1] for row in cursor.fetchall()}

    cursor.execute("SELECT COUNT(*) FROM terms")
    stats['total_terms'] = cursor.fetchone()[0]

    # Tag stats
    cursor.execute("SELECT COUNT(DISTINCT tag) FROM tags")
    result = cursor.fetchone()
    stats['unique_tags'] = result[0] if result else 0

    cursor.execute("SELECT COUNT(DISTINCT normalized_tag) FROM tags")
    result = cursor.fetchone()
    stats['normalized_unique_tags'] = result[0] if result else 0

    # Top tags
    cursor.execute("""
        SELECT normalized_tag, COUNT(*) as count
        FROM tags
        WHERE normalized_tag IS NOT NULL
        GROUP BY normalized_tag
        ORDER BY count DESC
        LIMIT 20
    """)
    stats['top_tags'] = {row['normalized_tag']: row[1] for row in cursor.fetchall()}

    conn.close()
    return stats


def search_fts(query, space=None, file_type=None, limit=20):
    """Full-text search across files."""
    conn = get_connection(space)
    cursor = conn.cursor()

    if file_type:
        cursor.execute("""
            SELECT f.id, f.title, f.path, f.space, f.type, f.maturity,
                   snippet(files_fts, 1, '<b>', '</b>', '...', 32) as snippet
            FROM files_fts
            JOIN files f ON files_fts.rowid = f.rowid
            WHERE files_fts MATCH ? AND f.type = ?
            ORDER BY rank
            LIMIT ?
        """, (query, file_type, limit))
    else:
        cursor.execute("""
            SELECT f.id, f.title, f.path, f.space, f.type, f.maturity,
                   snippet(files_fts, 1, '<b>', '</b>', '...', 32) as snippet
            FROM files_fts
            JOIN files f ON files_fts.rowid = f.rowid
            WHERE files_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, limit))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def find_similar(file_id, space=None, limit=10):
    """Find files with similar terms."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("SELECT term FROM terms WHERE file_id = ?", (file_id,))
    terms = [row['term'] for row in cursor.fetchall()]

    if not terms:
        conn.close()
        return []

    placeholders = ','.join(['?' for _ in terms])
    cursor.execute(f"""
        SELECT f.id, f.title, f.path, f.space, f.type, f.maturity,
               COUNT(t.term) as shared_terms
        FROM files f
        JOIN terms t ON f.id = t.file_id
        WHERE t.term IN ({placeholders})
          AND f.id != ?
        GROUP BY f.id
        ORDER BY shared_terms DESC
        LIMIT ?
    """, (*terms, file_id, limit))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def find_backlinks(file_id=None, title=None, space=None):
    """Find all files that link to this one."""
    conn = get_connection(space)
    cursor = conn.cursor()

    if file_id:
        cursor.execute("""
            SELECT f.id, f.title, f.path, f.space, f.type
            FROM links l
            JOIN files f ON l.source_id = f.id
            WHERE l.target_id = ?
        """, (file_id,))
    elif title:
        cursor.execute("""
            SELECT f.id, f.title, f.path, f.space, f.type
            FROM links l
            JOIN files f ON l.source_id = f.id
            WHERE l.target_title = ?
        """, (title,))
    else:
        conn.close()
        return []

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_unresolved_links(space=None, min_refs=1):
    """Get all unresolved link targets with reference counts."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT target_title, COUNT(*) as reference_count,
               GROUP_CONCAT(DISTINCT f.space) as from_spaces
        FROM links l
        JOIN files f ON l.source_id = f.id
        WHERE l.resolved = 0
        GROUP BY target_title
        HAVING COUNT(*) >= ?
        ORDER BY reference_count DESC
    """, (min_refs,))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_orphans(space=None, file_type=None):
    """Find files with no incoming links."""
    conn = get_connection(space)
    cursor = conn.cursor()

    if file_type:
        cursor.execute("""
            SELECT f.id, f.title, f.path, f.space, f.type, f.maturity
            FROM files f
            LEFT JOIN links l ON f.id = l.target_id
            WHERE l.target_id IS NULL
              AND f.is_stub = 0
              AND f.type = ?
        """, (file_type,))
    else:
        cursor.execute("""
            SELECT f.id, f.title, f.path, f.space, f.type, f.maturity
            FROM files f
            LEFT JOIN links l ON f.id = l.target_id
            WHERE l.target_id IS NULL
              AND f.is_stub = 0
        """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def sync_to_root(space):
    """Sync a space DB to the root DB."""
    if space not in SPACES:
        raise ValueError(f"Unknown space: {space}")

    space_conn = get_connection(space)
    root_conn = get_connection(None)

    space_cursor = space_conn.cursor()
    root_cursor = root_conn.cursor()

    # Sync files (including author column)
    space_cursor.execute("""
        SELECT id, path, space, type, title, content, summary, word_count,
               maturity, is_stub, author, created_at, updated_at, processed_at
        FROM files
    """)
    for row in space_cursor.fetchall():
        root_cursor.execute("""
            INSERT OR REPLACE INTO files
            (id, path, space, type, title, content, summary, word_count,
             maturity, is_stub, author, created_at, updated_at, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, tuple(row))

    # Sync terms
    space_cursor.execute("SELECT file_id, term, frequency, is_entity, entity_type FROM terms")
    for row in space_cursor.fetchall():
        root_cursor.execute("""
            INSERT OR IGNORE INTO terms (file_id, term, frequency, is_entity, entity_type)
            VALUES (?, ?, ?, ?, ?)
        """, tuple(row))

    # Sync links/references (including syntax column)
    space_cursor.execute("SELECT source_id, target_id, target_title, link_type, syntax, resolved, created_at FROM links")
    for row in space_cursor.fetchall():
        root_cursor.execute("""
            INSERT OR IGNORE INTO links (source_id, target_id, target_title, link_type, syntax, resolved, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, tuple(row))

    # Sync tags
    space_cursor.execute("SELECT file_id, tag, normalized_tag FROM tags")
    for row in space_cursor.fetchall():
        root_cursor.execute("""
            INSERT OR IGNORE INTO tags (file_id, tag, normalized_tag)
            VALUES (?, ?, ?)
        """, tuple(row))

    root_conn.commit()
    space_conn.close()
    root_conn.close()
    print(f"Synced {space} to root DB")


def sync_all_to_root():
    """Sync all space DBs to root."""
    for space in SPACES:
        space_db = get_db_path(space)
        if space_db.exists():
            sync_to_root(space)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Knowledge Database Manager")
    parser.add_argument('command', choices=['init', 'init-all', 'stats', 'search', 'unresolved', 'orphans', 'sync', 'sync-all'])
    parser.add_argument('query', nargs='?', help='Search query (for search command)')
    parser.add_argument('--space', '-s', choices=list(SPACES.keys()), help='Space to operate on (omit for root)')
    parser.add_argument('--type', '-t', help='Filter by file type (zettel, page, journal, etc.)')

    args = parser.parse_args()

    if args.command == "init":
        init_database(args.space)

    elif args.command == "init-all":
        init_all_databases()

    elif args.command == "stats":
        stats = get_stats(args.space, args.type)
        space_label = args.space if args.space else "root (all spaces)"
        print(f"\n=== Knowledge Database Stats ({space_label}) ===")
        print(f"Total files: {stats['total_files']}")
        print(f"Stubs: {stats['stubs']}")
        print(f"Total references: {stats['total_links']}")
        print(f"Resolved: {stats['resolved_links']}")
        print(f"Unresolved targets: {stats['unresolved_targets']}")
        if stats.get('by_syntax'):
            print("  By syntax:")
            for syntax, count in stats['by_syntax'].items():
                icon = {'wiki-link': '[[]]', 'hashtag': '#tag', 'hashtag-bracket': '#[[]]'}.get(syntax, syntax)
                print(f"    {icon}: {count}")
        print(f"Total terms: {stats['total_terms']}")
        print(f"Unique tags: {stats['unique_tags']} (normalized: {stats['normalized_unique_tags']})")
        print("\nBy space:")
        for space, count in stats.get('by_space', {}).items():
            print(f"  {space}: {count}")
        print("\nBy type:")
        for ftype, count in stats.get('by_type', {}).items():
            print(f"  {ftype}: {count}")
        print("\nBy author:")
        for author, count in stats.get('by_author', {}).items():
            print(f"  {author}: {count}")
        if stats.get('by_maturity'):
            print("\nZettel maturity:")
            for maturity, count in stats.get('by_maturity', {}).items():
                print(f"  {maturity}: {count}")
        if stats.get('top_tags'):
            print("\nTop 10 tags:")
            for i, (tag, count) in enumerate(list(stats['top_tags'].items())[:10]):
                print(f"  {tag}: {count}")

    elif args.command == "search":
        if not args.query:
            print("Usage: python zettel_db.py search <query> [--space SPACE] [--type TYPE]")
            exit(1)
        results = search_fts(args.query, args.space, args.type)
        print(f"\n=== Search: '{args.query}' ===")
        for r in results:
            print(f"[{r['space']}/{r['type']}] {r['title']}")
            print(f"  {r['path']}")
            if r.get('snippet'):
                print(f"  ...{r['snippet']}...")
            print()

    elif args.command == "unresolved":
        results = get_unresolved_links(args.space)
        print("\n=== Unresolved Links ===")
        for r in results[:50]:
            print(f"  [{r['reference_count']}x] {r['target_title']} (from: {r['from_spaces']})")

    elif args.command == "orphans":
        results = get_orphans(args.space, args.type)
        print(f"\n=== Orphan Files ({len(results)}) ===")
        for r in results[:30]:
            print(f"  [{r['space']}/{r['type']}] {r['title']}")

    elif args.command == "sync":
        if not args.space:
            print("Usage: python zettel_db.py sync --space SPACE")
            exit(1)
        sync_to_root(args.space)

    elif args.command == "sync-all":
        sync_all_to_root()
