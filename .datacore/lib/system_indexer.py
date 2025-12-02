#!/usr/bin/env python3
"""
System Component Indexer (DIP-0004)

Indexes system components to the database:
- Agents (agent definitions)
- Commands (slash commands)
- DIPs (Datacore Improvement Proposals)
- Specs (specifications)
- Learning entries (patterns, corrections, preferences)
- Modules (installed modules)

Core Principle: Index system files for discovery, routing, and validation.

Usage:
    python system_indexer.py --sync
    python system_indexer.py --agents
    python system_indexer.py --commands
    python system_indexer.py --dips
    python system_indexer.py --learning
"""

import re
import sys
import yaml
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple

# Add lib to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from zettel_db import (
    get_connection, init_database, SPACES, DATA_ROOT, SYSTEM_PATHS
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


def extract_description(content: str) -> Optional[str]:
    """Extract description from markdown content.

    Looks for first paragraph after title or frontmatter.
    """
    lines = content.split('\n')
    in_paragraph = False
    paragraph_lines = []

    for line in lines:
        # Skip empty lines and headings at start
        if not line.strip():
            if in_paragraph:
                break
            continue

        if line.startswith('#'):
            in_paragraph = False
            paragraph_lines = []
            continue

        # Start of paragraph
        in_paragraph = True
        paragraph_lines.append(line.strip())

    if paragraph_lines:
        return ' '.join(paragraph_lines)[:500]
    return None


def extract_when_to_use(content: str) -> Optional[str]:
    """Extract 'When to Use' section from content."""
    pattern = re.compile(
        r'##\s*When to Use.*?\n(.*?)(?=\n##|\Z)',
        re.DOTALL | re.IGNORECASE
    )
    match = pattern.search(content)
    if match:
        text = match.group(1).strip()
        # Clean up markdown
        text = re.sub(r'\n+', ' ', text)
        return text[:500]
    return None


def parse_agent_file(file_path: Path) -> Dict[str, Any]:
    """Parse an agent definition file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    frontmatter, body = parse_frontmatter(content)
    checksum = compute_checksum(content)

    name = file_path.stem
    description = frontmatter.get('description') or extract_description(body)
    when_to_use = extract_when_to_use(body)

    return {
        'type': 'agent',
        'name': name,
        'description': description,
        'when_to_use': when_to_use,
        'path': str(file_path),
        'provides': frontmatter.get('provides'),
        'dependencies': frontmatter.get('dependencies'),
        'module': frontmatter.get('module'),
        'version': frontmatter.get('version'),
        'checksum': checksum,
    }


def parse_command_file(file_path: Path) -> Dict[str, Any]:
    """Parse a command definition file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    frontmatter, body = parse_frontmatter(content)
    checksum = compute_checksum(content)

    name = file_path.stem
    description = frontmatter.get('description') or extract_description(body)
    when_to_use = extract_when_to_use(body)

    # Extract triggers (natural language patterns that invoke this command)
    triggers = frontmatter.get('triggers', [])
    if not triggers:
        # Look for trigger patterns in content
        trigger_pattern = re.compile(r'(?:trigger|invoke|use this when)[:\s]*(.+?)(?:\n|$)', re.IGNORECASE)
        for match in trigger_pattern.finditer(body):
            triggers.append(match.group(1).strip())

    return {
        'type': 'command',
        'name': name,
        'description': description,
        'when_to_use': when_to_use,
        'triggers': triggers if triggers else None,
        'path': str(file_path),
        'provides': frontmatter.get('provides'),
        'dependencies': frontmatter.get('dependencies'),
        'module': frontmatter.get('module'),
        'version': frontmatter.get('version'),
        'checksum': checksum,
    }


def parse_dip_file(file_path: Path) -> Dict[str, Any]:
    """Parse a DIP (Datacore Improvement Proposal) file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    frontmatter, body = parse_frontmatter(content)
    checksum = compute_checksum(content)

    # Extract DIP number from filename (DIP-0001-name.md -> 1)
    name = file_path.stem
    number_match = re.search(r'DIP-?(\d+)', name, re.IGNORECASE)
    number = int(number_match.group(1)) if number_match else 0

    # Extract title from first # heading or frontmatter
    title = frontmatter.get('title')
    if not title:
        title_match = re.search(r'^#\s+(.+)$', body, re.MULTILINE)
        if title_match:
            title = title_match.group(1).strip()
        else:
            title = name

    # Extract status from table or frontmatter
    status = frontmatter.get('status')
    if not status:
        status_match = re.search(r'\|\s*\*?\*?Status\*?\*?\s*\|\s*(\w+)\s*\|', body)
        if status_match:
            status = status_match.group(1)

    # Extract abstract
    abstract = frontmatter.get('abstract')
    if not abstract:
        abstract_match = re.search(r'##\s*Abstract\s*\n(.*?)(?=\n##|\Z)', body, re.DOTALL)
        if abstract_match:
            abstract = abstract_match.group(1).strip()[:1000]

    # Extract affects
    affects = frontmatter.get('affects')
    if not affects:
        affects_match = re.search(r'\|\s*\*?\*?Affects\*?\*?\s*\|\s*(.+?)\s*\|', body)
        if affects_match:
            affects = affects_match.group(1).strip()

    return {
        'number': number,
        'title': title,
        'status': status,
        'abstract': abstract,
        'affects': affects,
        'related_specs': frontmatter.get('related_specs'),
        'related_dips': frontmatter.get('related_dips'),
        'author': frontmatter.get('author'),
        'path': str(file_path),
        'checksum': checksum,
    }


def parse_spec_file(file_path: Path) -> Dict[str, Any]:
    """Parse a specification file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    frontmatter, body = parse_frontmatter(content)
    checksum = compute_checksum(content)

    name = file_path.stem
    category = frontmatter.get('category')

    # Try to detect category from path
    if not category:
        path_str = str(file_path)
        if 'agent' in path_str.lower():
            category = 'agent'
        elif 'workflow' in path_str.lower():
            category = 'workflow'
        elif 'schema' in path_str.lower():
            category = 'schema'
        else:
            category = 'general'

    return {
        'name': name,
        'category': category,
        'version': frontmatter.get('version', '1.0'),
        'content': body[:10000],  # Limit content size
        'related_dips': frontmatter.get('related_dips'),
        'path': str(file_path),
        'checksum': checksum,
    }


def parse_learning_file(file_path: Path) -> List[Dict[str, Any]]:
    """Parse a learning file (patterns.md, corrections.md, preferences.md).

    Returns list of learning entries.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    frontmatter, body = parse_frontmatter(content)
    checksum = compute_checksum(content)

    # Determine type from filename
    filename = file_path.stem.lower()
    if 'pattern' in filename:
        entry_type = 'pattern'
    elif 'correction' in filename:
        entry_type = 'correction'
    elif 'preference' in filename:
        entry_type = 'preference'
    else:
        entry_type = 'note'

    entries = []

    # Parse entries (usually ## headings with content)
    entry_pattern = re.compile(r'^##\s+(.+)$', re.MULTILINE)
    matches = list(entry_pattern.finditer(body))

    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start_pos = match.end()

        # Find end of entry
        if i + 1 < len(matches):
            end_pos = matches[i + 1].start()
        else:
            end_pos = len(body)

        entry_content = body[start_pos:end_pos].strip()

        # Extract tags from content
        tags = []
        tag_match = re.search(r'Tags?:\s*(.+?)(?:\n|$)', entry_content, re.IGNORECASE)
        if tag_match:
            tags = [t.strip() for t in tag_match.group(1).split(',')]

        # Extract applies_to
        applies_to = []
        applies_match = re.search(r'Applies to:\s*(.+?)(?:\n|$)', entry_content, re.IGNORECASE)
        if applies_match:
            applies_to = [a.strip() for a in applies_match.group(1).split(',')]

        entries.append({
            'type': entry_type,
            'title': title,
            'content': entry_content,
            'tags': tags if tags else None,
            'applies_to': applies_to if applies_to else None,
            'source_file': str(file_path),
            'checksum': checksum,
        })

    # If no ## entries, treat whole file as one entry
    if not entries and body.strip():
        entries.append({
            'type': entry_type,
            'title': file_path.stem,
            'content': body,
            'tags': None,
            'applies_to': None,
            'source_file': str(file_path),
            'checksum': checksum,
        })

    return entries


def index_agents(verbose: bool = True) -> int:
    """Index all agent definition files."""
    agents_path = SYSTEM_PATHS['agents']

    if not agents_path.exists():
        print(f"Agents path not found: {agents_path}")
        return 0

    conn = get_connection(None)  # Root DB
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    count = 0
    for file_path in agents_path.glob('*.md'):
        if file_path.name.startswith('.'):
            continue

        try:
            agent = parse_agent_file(file_path)

            cursor.execute("""
                INSERT OR REPLACE INTO system_components
                (type, name, description, path, module, provides, dependencies,
                 when_to_use, source_file, checksum, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                agent['type'],
                agent['name'],
                agent['description'],
                agent['path'],
                agent['module'],
                str(agent['provides']) if agent['provides'] else None,
                str(agent['dependencies']) if agent['dependencies'] else None,
                agent['when_to_use'],
                str(file_path),
                agent['checksum'],
                now,
                now
            ))
            count += 1

            if verbose:
                print(f"  Indexed agent: {agent['name']}")
        except Exception as e:
            print(f"  Error indexing {file_path.name}: {e}")

    conn.commit()
    conn.close()
    return count


def index_commands(verbose: bool = True) -> int:
    """Index all command definition files."""
    commands_path = SYSTEM_PATHS['commands']

    if not commands_path.exists():
        print(f"Commands path not found: {commands_path}")
        return 0

    conn = get_connection(None)  # Root DB
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    count = 0
    for file_path in commands_path.glob('*.md'):
        if file_path.name.startswith('.'):
            continue

        try:
            command = parse_command_file(file_path)

            cursor.execute("""
                INSERT OR REPLACE INTO system_components
                (type, name, description, path, module, provides, dependencies,
                 triggers, when_to_use, source_file, checksum, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                command['type'],
                command['name'],
                command['description'],
                command['path'],
                command['module'],
                str(command['provides']) if command['provides'] else None,
                str(command['dependencies']) if command['dependencies'] else None,
                str(command['triggers']) if command['triggers'] else None,
                command['when_to_use'],
                str(file_path),
                command['checksum'],
                now,
                now
            ))
            count += 1

            if verbose:
                print(f"  Indexed command: {command['name']}")
        except Exception as e:
            print(f"  Error indexing {file_path.name}: {e}")

    conn.commit()
    conn.close()
    return count


def index_dips(verbose: bool = True) -> int:
    """Index all DIP files."""
    dips_path = SYSTEM_PATHS['dips']

    if not dips_path.exists():
        print(f"DIPs path not found: {dips_path}")
        return 0

    conn = get_connection(None)  # Root DB
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    count = 0
    for file_path in dips_path.glob('DIP-*.md'):
        if file_path.name.startswith('.'):
            continue

        # Skip non-DIP files like README.md
        if not re.match(r'^DIP-\d{4}', file_path.name):
            continue

        try:
            dip = parse_dip_file(file_path)

            cursor.execute("""
                INSERT OR REPLACE INTO dips
                (number, title, status, abstract, affects, related_specs,
                 related_dips, author, source_file, checksum, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                dip['number'],
                dip['title'],
                dip['status'],
                dip['abstract'],
                dip['affects'],
                str(dip['related_specs']) if dip['related_specs'] else None,
                str(dip['related_dips']) if dip['related_dips'] else None,
                dip['author'],
                str(file_path),
                dip['checksum'],
                now,
                now
            ))
            count += 1

            if verbose:
                print(f"  Indexed DIP-{dip['number']:04d}: {dip['title']}")
        except Exception as e:
            print(f"  Error indexing {file_path.name}: {e}")

    conn.commit()
    conn.close()
    return count


def index_specs(verbose: bool = True) -> int:
    """Index all specification files."""
    specs_path = SYSTEM_PATHS['specs']

    if not specs_path.exists():
        if verbose:
            print(f"Specs path not found: {specs_path}")
        return 0

    conn = get_connection(None)  # Root DB
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    count = 0
    for file_path in specs_path.glob('*.md'):
        if file_path.name.startswith('.'):
            continue

        try:
            spec = parse_spec_file(file_path)

            cursor.execute("""
                INSERT OR REPLACE INTO specs
                (name, category, version, content, related_dips,
                 source_file, checksum, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                spec['name'],
                spec['category'],
                spec['version'],
                spec['content'],
                str(spec['related_dips']) if spec['related_dips'] else None,
                str(file_path),
                spec['checksum'],
                now,
                now
            ))
            count += 1

            if verbose:
                print(f"  Indexed spec: {spec['name']}")
        except Exception as e:
            print(f"  Error indexing {file_path.name}: {e}")

    conn.commit()
    conn.close()
    return count


def index_learning(verbose: bool = True) -> int:
    """Index all learning files."""
    learning_path = SYSTEM_PATHS['learning']

    if not learning_path.exists():
        if verbose:
            print(f"Learning path not found: {learning_path}")
        return 0

    conn = get_connection(None)  # Root DB
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    # Clear existing learning entries
    cursor.execute("DELETE FROM learning_entries")

    count = 0
    for file_path in learning_path.glob('*.md'):
        if file_path.name.startswith('.'):
            continue

        try:
            entries = parse_learning_file(file_path)

            for entry in entries:
                cursor.execute("""
                    INSERT INTO learning_entries
                    (type, title, content, source_file, tags, applies_to, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    entry['type'],
                    entry['title'],
                    entry['content'],
                    entry['source_file'],
                    str(entry['tags']) if entry['tags'] else None,
                    str(entry['applies_to']) if entry['applies_to'] else None,
                    now,
                    now
                ))
                count += 1

            if verbose:
                print(f"  Indexed {len(entries)} entries from {file_path.name}")
        except Exception as e:
            print(f"  Error indexing {file_path.name}: {e}")

    conn.commit()
    conn.close()
    return count


def sync_system_components(verbose: bool = True) -> Dict[str, int]:
    """Sync all system components to database."""
    init_database(None)  # Ensure root DB exists

    stats = {
        'agents': 0,
        'commands': 0,
        'dips': 0,
        'specs': 0,
        'learning': 0,
    }

    if verbose:
        print("\n=== Indexing System Components ===")

    if verbose:
        print("\n[1/5] Agents...")
    stats['agents'] = index_agents(verbose)

    if verbose:
        print("\n[2/5] Commands...")
    stats['commands'] = index_commands(verbose)

    if verbose:
        print("\n[3/5] DIPs...")
    stats['dips'] = index_dips(verbose)

    if verbose:
        print("\n[4/5] Specs...")
    stats['specs'] = index_specs(verbose)

    if verbose:
        print("\n[5/5] Learning entries...")
    stats['learning'] = index_learning(verbose)

    return stats


def get_agents(module: str = None) -> List[Dict[str, Any]]:
    """Get all indexed agents."""
    conn = get_connection(None)
    cursor = conn.cursor()

    if module:
        cursor.execute(
            "SELECT * FROM system_components WHERE type = 'agent' AND module = ?",
            (module,)
        )
    else:
        cursor.execute("SELECT * FROM system_components WHERE type = 'agent'")

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_commands(module: str = None) -> List[Dict[str, Any]]:
    """Get all indexed commands."""
    conn = get_connection(None)
    cursor = conn.cursor()

    if module:
        cursor.execute(
            "SELECT * FROM system_components WHERE type = 'command' AND module = ?",
            (module,)
        )
    else:
        cursor.execute("SELECT * FROM system_components WHERE type = 'command'")

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_dips(status: str = None) -> List[Dict[str, Any]]:
    """Get all indexed DIPs."""
    conn = get_connection(None)
    cursor = conn.cursor()

    if status:
        cursor.execute("SELECT * FROM dips WHERE status = ? ORDER BY number", (status,))
    else:
        cursor.execute("SELECT * FROM dips ORDER BY number")

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def get_learning_entries(entry_type: str = None) -> List[Dict[str, Any]]:
    """Get learning entries, optionally filtered by type."""
    conn = get_connection(None)
    cursor = conn.cursor()

    if entry_type:
        cursor.execute(
            "SELECT * FROM learning_entries WHERE type = ? ORDER BY created_at DESC",
            (entry_type,)
        )
    else:
        cursor.execute("SELECT * FROM learning_entries ORDER BY type, created_at DESC")

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def print_stats(stats: Dict[str, int]):
    """Print indexing statistics."""
    print(f"\n{'='*50}")
    print("SYSTEM INDEXING COMPLETE")
    print(f"{'='*50}")
    print(f"Agents: {stats['agents']}")
    print(f"Commands: {stats['commands']}")
    print(f"DIPs: {stats['dips']}")
    print(f"Specs: {stats['specs']}")
    print(f"Learning entries: {stats['learning']}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="System Component Indexer")
    parser.add_argument('--sync', action='store_true', help='Sync all system components')
    parser.add_argument('--agents', action='store_true', help='Index agents only')
    parser.add_argument('--commands', action='store_true', help='Index commands only')
    parser.add_argument('--dips', action='store_true', help='Index DIPs only')
    parser.add_argument('--specs', action='store_true', help='Index specs only')
    parser.add_argument('--learning', action='store_true', help='Index learning files only')
    parser.add_argument('--list', action='store_true', help='List indexed items')
    parser.add_argument('--type', choices=['agent', 'command', 'dip', 'learning'], help='Type to list')

    args = parser.parse_args()

    if args.sync:
        stats = sync_system_components()
        print_stats(stats)

    elif args.agents:
        init_database(None)
        count = index_agents()
        print(f"\nIndexed {count} agents")

    elif args.commands:
        init_database(None)
        count = index_commands()
        print(f"\nIndexed {count} commands")

    elif args.dips:
        init_database(None)
        count = index_dips()
        print(f"\nIndexed {count} DIPs")

    elif args.specs:
        init_database(None)
        count = index_specs()
        print(f"\nIndexed {count} specs")

    elif args.learning:
        init_database(None)
        count = index_learning()
        print(f"\nIndexed {count} learning entries")

    elif args.list:
        if args.type == 'agent':
            items = get_agents()
            print(f"\n=== Agents ({len(items)}) ===")
            for item in items:
                print(f"  {item['name']}: {item['description'][:60] if item['description'] else 'No description'}...")
        elif args.type == 'command':
            items = get_commands()
            print(f"\n=== Commands ({len(items)}) ===")
            for item in items:
                print(f"  /{item['name']}: {item['description'][:60] if item['description'] else 'No description'}...")
        elif args.type == 'dip':
            items = get_dips()
            print(f"\n=== DIPs ({len(items)}) ===")
            for item in items:
                print(f"  DIP-{item['number']:04d}: {item['title']} [{item['status']}]")
        elif args.type == 'learning':
            items = get_learning_entries()
            print(f"\n=== Learning Entries ({len(items)}) ===")
            for item in items:
                print(f"  [{item['type']}] {item['title']}")
        else:
            # List counts
            print("\n=== System Components ===")
            print(f"Agents: {len(get_agents())}")
            print(f"Commands: {len(get_commands())}")
            print(f"DIPs: {len(get_dips())}")
            print(f"Learning: {len(get_learning_entries())}")

    else:
        parser.print_help()
