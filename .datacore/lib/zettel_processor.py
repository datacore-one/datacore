#!/usr/bin/env python3
"""
Knowledge Processor

Processes ALL markdown files:
- Extracts frontmatter and content
- Finds wiki-links [[target]]
- Updates SQLite database
- Creates stubs for unresolved links
- Injects backlinks into zettels

Usage:
    python zettel_processor.py <file_path>
    python zettel_processor.py --scan --space SPACE
    python zettel_processor.py --resolve-links [--space SPACE]
    python zettel_processor.py --create-stubs [--space SPACE]
    python zettel_processor.py --inject-backlinks [--space SPACE]
    python zettel_processor.py --full-process [--space SPACE]
"""

import os
import re
import sys
import yaml
from pathlib import Path
from datetime import datetime
from collections import Counter

# Add lib to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from zettel_db import (
    get_connection, init_database, get_db_path, detect_file_type,
    detect_author, SPACES, DATA_ROOT, sync_to_root
)


def parse_frontmatter(content):
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


def extract_references(content):
    """Extract all page references from content (Roam-style).

    Extracts:
    - [[Page Reference]] - standard wiki links
    - #[[Multi Word Tag]] - hashtag with brackets
    - #tag - simple hashtag (single word)

    All three are equivalent - they create/reference pages.
    Returns list of dicts with 'target' and 'syntax' keys.
    """
    references = []
    seen = set()  # Deduplicate
    hashtag_bracket_targets = set()  # Track #[[...]] to distinguish from [[...]]

    # 1. #[[Multi Word Tag]] - hashtag with brackets (check first!)
    hashtag_bracket_pattern = r'#\[\[([^\]]+)\]\]'
    for match in re.findall(hashtag_bracket_pattern, content):
        target = match.strip()
        if target:
            hashtag_bracket_targets.add(target.lower())
            if target.lower() not in seen:
                references.append({'target': target, 'syntax': 'hashtag-bracket'})
                seen.add(target.lower())

    # 2. [[Page Reference]] - standard wiki links (skip if already captured as #[[...]])
    wiki_pattern = r'(?<!#)\[\[([^\]]+)\]\]'
    for match in re.findall(wiki_pattern, content):
        # Handle alias syntax [[target|display]]
        if '|' in match:
            target = match.split('|')[0].strip()
        else:
            target = match.strip()

        if target and target.lower() not in seen:
            references.append({'target': target, 'syntax': 'wiki-link'})
            seen.add(target.lower())

    # 3. #tag - simple hashtag (word characters, hyphens allowed)
    simple_hashtag_pattern = r'(?<!\[)#([a-zA-Z][a-zA-Z0-9_-]*)'
    for match in re.findall(simple_hashtag_pattern, content):
        target = match.strip()
        if target and target.lower() not in seen:
            references.append({'target': target, 'syntax': 'hashtag'})
            seen.add(target.lower())

    return references


def extract_wiki_links(content):
    """Extract all [[wiki links]] from content.
    DEPRECATED: Use extract_references() instead.
    Kept for backward compatibility.
    """
    refs = extract_references(content)
    return [r['target'] for r in refs]


def normalize_tag(tag):
    """Normalize a tag for consistency.

    Rules:
    - Lowercase
    - Singular form (basic plurals: -s, -es, -ies)
    - Hyphens instead of spaces/underscores
    - No special characters
    """
    if not tag:
        return None

    # Lowercase and strip
    normalized = tag.lower().strip()

    # Replace spaces and underscores with hyphens
    normalized = re.sub(r'[\s_]+', '-', normalized)

    # Remove special characters except hyphens
    normalized = re.sub(r'[^a-z0-9-]', '', normalized)

    # Remove multiple hyphens
    normalized = re.sub(r'-+', '-', normalized).strip('-')

    # Basic singularization (conservative approach)
    if normalized.endswith('ies') and len(normalized) > 4:
        # technologies -> technology, strategies -> strategy
        normalized = normalized[:-3] + 'y'
    elif normalized.endswith('sses') or normalized.endswith('xes') or normalized.endswith('shes') or normalized.endswith('ches'):
        # classes -> class, boxes -> box
        normalized = normalized[:-2]
    elif normalized.endswith('s') and not normalized.endswith('ss') and len(normalized) > 3:
        # Only remove trailing 's' if it's likely plural (not 'analysis', 'chaos', etc.)
        # Preserve words that naturally end in 's'
        preserve = ['status', 'analysis', 'basis', 'crisis', 'thesis', 'diagnosis',
                   'praxis', 'process', 'success', 'progress', 'access', 'focus',
                   'hypothesis', 'synopsis', 'ellipsis', 'emphasis', 'synthesis']
        if normalized not in preserve:
            normalized = normalized[:-1]

    return normalized if normalized else None


def extract_tags(frontmatter):
    """Extract and normalize tags from frontmatter."""
    tags_raw = frontmatter.get('tags', [])

    # Handle string format "tag1, tag2, tag3"
    if isinstance(tags_raw, str):
        tags_raw = [t.strip() for t in tags_raw.split(',')]

    # Handle list format [tag1, tag2, tag3]
    if not isinstance(tags_raw, list):
        tags_raw = [str(tags_raw)]

    tags = []
    for tag in tags_raw:
        if tag and isinstance(tag, str):
            normalized = normalize_tag(tag)
            if normalized:
                tags.append({
                    'original': tag.strip(),
                    'normalized': normalized
                })

    return tags


def extract_terms(content, min_length=4):
    """Extract significant terms from content for similarity matching."""
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', content)
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
    text = re.sub(r'[#*`_~]', '', text)
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`]+`', '', text)

    words = re.findall(r'\b[a-zA-Z][a-zA-Z0-9-]*[a-zA-Z0-9]\b', text.lower())

    stopwords = {
        'the', 'and', 'for', 'that', 'this', 'with', 'from', 'are', 'was',
        'were', 'been', 'have', 'has', 'had', 'but', 'not', 'you', 'all',
        'can', 'her', 'his', 'they', 'will', 'would', 'could', 'should',
        'their', 'what', 'when', 'where', 'which', 'who', 'how', 'why',
        'each', 'more', 'other', 'some', 'such', 'than', 'then', 'these',
        'into', 'about', 'after', 'before', 'between', 'through', 'during',
        'without', 'also', 'just', 'only', 'very', 'even', 'most', 'being',
        'does', 'did', 'doing', 'here', 'there', 'itself', 'those', 'once'
    }

    filtered = [w for w in words if len(w) >= min_length and w not in stopwords]
    return Counter(filtered)


def detect_entities(content):
    """Detect named entities (simple heuristic-based)."""
    entities = []

    proper_nouns = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b', content)
    for noun in proper_nouns:
        if len(noun) > 3 and noun not in ['The', 'This', 'That', 'These', 'Those']:
            entities.append({'term': noun.lower(), 'type': 'concept'})

    acronyms = re.findall(r'\b([A-Z]{2,})\b', content)
    for acr in acronyms:
        if acr not in ['TODO', 'NOTE', 'YAML', 'JSON', 'HTML', 'HTTP', 'HTTPS']:
            entities.append({'term': acr.lower(), 'type': 'acronym'})

    return entities


def get_space_from_path(path):
    """Determine which space a file belongs to."""
    path_str = str(path)
    for space, config in SPACES.items():
        if str(config['path']) in path_str:
            return space
    return 'unknown'


def generate_file_id(path, frontmatter):
    """Generate unique file ID."""
    stem = Path(path).stem
    clean_id = re.sub(r'[^a-zA-Z0-9-]', '-', stem.lower())
    clean_id = re.sub(r'-+', '-', clean_id).strip('-')

    if 'id' in frontmatter:
        fm_id = str(frontmatter['id'])
        if not re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', fm_id):
            return fm_id

    return clean_id


def process_file(path, space=None, dry_run=False):
    """Process a single markdown file."""
    path = Path(path)
    if not path.exists():
        return None

    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        try:
            with open(path, 'r', encoding='latin-1') as f:
                content = f.read()
        except Exception:
            print(f"  Skipping {path.name}: encoding error")
            return None

    frontmatter, body = parse_frontmatter(content)

    if space is None:
        space = get_space_from_path(path)

    file_id = generate_file_id(path, frontmatter)
    file_type = detect_file_type(path)
    title = frontmatter.get('title', path.stem.replace('-', ' ').replace('_', ' '))

    # Extract all page references (Roam-style: [[link]], #tag, #[[tag]])
    references = extract_references(content)
    terms = extract_terms(body)
    entities = detect_entities(body)
    tags = extract_tags(frontmatter)

    word_count = len(body.split())
    is_stub = frontmatter.get('status') == 'stub' or word_count < 50

    # Detect author (human vs AI)
    author = detect_author(content, frontmatter, path)

    created = frontmatter.get('created', frontmatter.get('date', datetime.now().strftime('%Y-%m-%d')))
    updated = frontmatter.get('updated', datetime.now().strftime('%Y-%m-%d'))

    file_data = {
        'id': file_id,
        'path': str(path),
        'space': space,
        'type': file_type,
        'title': title,
        'content': body,
        'summary': frontmatter.get('summary', frontmatter.get('description', '')),
        'word_count': word_count,
        'maturity': frontmatter.get('maturity', 'seedling'),
        'is_stub': is_stub,
        'author': author,
        'created_at': str(created) if created else None,
        'updated_at': str(updated) if updated else None,
        'references': references,  # Roam-style: all [[]], #tag, #[[]] refs
        'terms': terms,
        'entities': entities,
        'tags': tags,  # Frontmatter tags only
    }

    if not dry_run:
        save_to_database(file_data, space)

    return file_data


def save_to_database(file_data, space=None):
    """Save file data to SQLite database."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR REPLACE INTO files
        (id, path, space, type, title, content, summary, word_count, maturity, is_stub, author, created_at, updated_at, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        file_data['id'],
        file_data['path'],
        file_data['space'],
        file_data['type'],
        file_data['title'],
        file_data['content'],
        file_data['summary'],
        file_data['word_count'],
        file_data['maturity'],
        file_data['is_stub'],
        file_data.get('author', 'unknown'),
        file_data['created_at'],
        file_data['updated_at'],
        datetime.now().isoformat()
    ))

    # Save terms
    cursor.execute("DELETE FROM terms WHERE file_id = ?", (file_data['id'],))
    for term, freq in file_data['terms'].items():
        cursor.execute("""
            INSERT INTO terms (file_id, term, frequency, is_entity, entity_type)
            VALUES (?, ?, ?, 0, NULL)
        """, (file_data['id'], term, freq))

    for entity in file_data['entities']:
        cursor.execute("""
            INSERT INTO terms (file_id, term, frequency, is_entity, entity_type)
            VALUES (?, ?, 1, 1, ?)
        """, (file_data['id'], entity['term'], entity['type']))

    # Save references (Roam-style: [[link]], #tag, #[[tag]] are all page refs)
    cursor.execute("DELETE FROM links WHERE source_id = ?", (file_data['id'],))
    for ref in file_data.get('references', []):
        cursor.execute("""
            INSERT INTO links (source_id, target_id, target_title, link_type, syntax, resolved, created_at)
            VALUES (?, NULL, ?, 'related', ?, 0, ?)
        """, (file_data['id'], ref['target'], ref['syntax'], datetime.now().isoformat()))

    # Save tags
    cursor.execute("DELETE FROM tags WHERE file_id = ?", (file_data['id'],))
    for tag_info in file_data.get('tags', []):
        cursor.execute("""
            INSERT INTO tags (file_id, tag, normalized_tag)
            VALUES (?, ?, ?)
        """, (file_data['id'], tag_info['original'], tag_info['normalized']))

    conn.commit()
    conn.close()


def resolve_links(space=None):
    """Resolve links by matching target_title to existing files."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT l.id, l.target_title, f.id as target_id
        FROM links l
        LEFT JOIN files f ON LOWER(f.title) = LOWER(l.target_title)
        WHERE l.resolved = 0
    """)

    updates = 0
    for row in cursor.fetchall():
        if row['target_id']:
            cursor.execute("""
                UPDATE links SET target_id = ?, resolved = 1 WHERE id = ?
            """, (row['target_id'], row['id']))
            updates += 1

    conn.commit()
    conn.close()
    print(f"Resolved {updates} links")
    return updates


def create_stub(title, referenced_by, space='datafund'):
    """Create a stub zettel for an unresolved link target."""
    if space not in SPACES:
        space = 'datafund'

    # Find zettel path in space
    zettel_path = None
    for scan_path in SPACES[space]['scan_paths']:
        if '/zettel' in str(scan_path):
            zettel_path = scan_path
            break

    if not zettel_path:
        zettel_path = SPACES[space]['path'] / '2-knowledge' / 'zettel'

    zettel_path.mkdir(parents=True, exist_ok=True)

    filename = re.sub(r'[^a-zA-Z0-9\s-]', '', title)
    filename = re.sub(r'\s+', '-', filename)
    filename = f"{filename}.md"

    stub_path = zettel_path / filename

    if stub_path.exists():
        return None

    stub_id = filename.replace('.md', '').lower()
    now = datetime.now().strftime('%Y-%m-%d')

    unique_refs = list(set(ref['title'] for ref in referenced_by))
    backlinks_section = "\n".join([f"- [[{ref}]]" for ref in sorted(unique_refs)])

    stub_content = f"""---
id: {stub_id}
title: {title}
type: zettel
status: stub
created: {now}
maturity: seedling
tags: [stub, needs-content]
---

# {title}

> This is a stub zettel. The concept is referenced but not yet documented.

## Referenced By

{backlinks_section}

## Suggested Content

Based on references, this zettel should cover:
- Definition of {title}
- Key characteristics and properties
- How it relates to the referencing concepts

---
*Auto-generated stub. Expand with actual content.*
"""

    with open(stub_path, 'w', encoding='utf-8') as f:
        f.write(stub_content)

    print(f"  Created stub: {stub_path.name}")
    return stub_path


def create_stubs_for_unresolved(space='datafund', min_references=2):
    """Create stub zettels for unresolved link targets with multiple references."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT l.target_title, f.id, f.title, f.path
        FROM links l
        JOIN files f ON l.source_id = f.id
        WHERE l.resolved = 0
        ORDER BY l.target_title
    """)

    targets = {}
    for row in cursor.fetchall():
        target = row['target_title']
        if target not in targets:
            targets[target] = []
        targets[target].append({
            'id': row['id'],
            'title': row['title'],
            'path': row['path']
        })

    conn.close()

    created = 0
    for target, refs in targets.items():
        if len(refs) >= min_references:
            if create_stub(target, refs, space):
                created += 1

    print(f"Created {created} stub zettels (min {min_references} refs)")
    return created


def inject_backlinks(file_path, space=None):
    """Inject 'Referenced By' section into a file."""
    path = Path(file_path)
    if not path.exists():
        return False

    # Only inject backlinks into zettels
    if detect_file_type(path) != 'zettel':
        return False

    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    frontmatter, body = parse_frontmatter(content)
    file_id = generate_file_id(path, frontmatter)
    title = frontmatter.get('title', path.stem)

    if space is None:
        space = get_space_from_path(path)

    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT f.title, f.path
        FROM links l
        JOIN files f ON l.source_id = f.id
        WHERE l.target_title = ? OR l.target_id = ?
    """, (title, file_id))

    backlinks = [dict(row) for row in cursor.fetchall()]
    conn.close()

    if not backlinks:
        return False

    unique_titles = list(set(bl['title'] for bl in backlinks))
    new_links_text = "\n".join([f"- [[{t}]]" for t in sorted(unique_titles)])

    if '## Referenced By' in body:
        pattern = r'(## Referenced By\n)[\s\S]*?(?=\n## |\n---|\Z)'
        replacement = f"## Referenced By\n\n{new_links_text}\n"
        body = re.sub(pattern, replacement, body)
    else:
        new_section = f"\n## Referenced By\n\n{new_links_text}\n"
        if '## Source' in body:
            body = body.replace('## Source', f"{new_section}\n## Source")
        elif '## Suggested Content' in body:
            body = body.replace('## Suggested Content', f"{new_section}\n## Suggested Content")
        else:
            body = body.rstrip() + "\n" + new_section

    if frontmatter:
        new_content = f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n{body}"
    else:
        new_content = body

    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    return True


def inject_all_backlinks(space=None):
    """Inject backlinks into all zettels that have incoming links."""
    conn = get_connection(space)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT f.path
        FROM files f
        JOIN links l ON l.target_id = f.id OR l.target_title = f.title
        WHERE f.type = 'zettel'
    """)

    paths = [row['path'] for row in cursor.fetchall()]
    conn.close()

    updated = 0
    for path in paths:
        if inject_backlinks(path, space):
            updated += 1

    print(f"Updated {updated} zettels with backlinks")
    return updated


def scan_space(space, verbose=True):
    """Scan all configured paths in a space."""
    if space not in SPACES:
        print(f"Unknown space: {space}")
        return []

    processed = []
    total = 0

    for scan_path in SPACES[space]['scan_paths']:
        if not scan_path.exists():
            continue

        if verbose:
            print(f"\n  Scanning: {scan_path.relative_to(DATA_ROOT)}")

        count = 0
        for path in scan_path.rglob('*.md'):
            if path.name.startswith('_') or path.name.startswith('.'):
                continue

            result = process_file(path, space)
            if result:
                processed.append(result)
                count += 1

        if verbose:
            print(f"    Processed {count} files")
        total += count

    if verbose:
        print(f"\n  Total: {total} files in {space}")

    return processed


def full_process(space=None, create_stubs_flag=True, inject_backlinks_flag=True):
    """Full processing pipeline."""
    print(f"\n{'='*60}")
    print(f"KNOWLEDGE DATABASE PROCESSING")
    print(f"{'='*60}")

    # Initialize DBs
    if space:
        init_database(space)
        spaces_to_process = [space]
    else:
        init_database(None)
        for sp in SPACES:
            init_database(sp)
        spaces_to_process = list(SPACES.keys())

    # Scan all spaces
    print("\n[1/6] Scanning files...")
    for sp in spaces_to_process:
        print(f"\n--- {sp.upper()} ---")
        scan_space(sp)

    # Resolve links
    print("\n[2/6] Resolving links...")
    for sp in spaces_to_process:
        resolve_links(sp)

    # Create stubs
    if create_stubs_flag:
        print("\n[3/6] Creating stubs for unresolved links...")
        for sp in spaces_to_process:
            create_stubs_for_unresolved(sp, min_references=2)

        # Re-scan to pick up stubs
        print("\n[4/6] Re-scanning to include stubs...")
        for sp in spaces_to_process:
            scan_space(sp, verbose=False)

        # Resolve again
        print("\n[5/6] Resolving links (including stubs)...")
        for sp in spaces_to_process:
            resolve_links(sp)

    # Inject backlinks
    if inject_backlinks_flag:
        print("\n[6/6] Injecting backlinks...")
        for sp in spaces_to_process:
            inject_all_backlinks(sp)

    # Sync to root
    print("\n--- Syncing to root DB ---")
    for sp in spaces_to_process:
        sync_to_root(sp)

    print(f"\n{'='*60}")
    print("PROCESSING COMPLETE")
    print(f"{'='*60}\n")


def print_summary(file_data):
    """Print summary of processed file."""
    print(f"\n=== {file_data['title']} ===")
    print(f"ID: {file_data['id']}")
    print(f"Space: {file_data['space']}")
    print(f"Type: {file_data['type']}")
    print(f"Author: {file_data.get('author', 'unknown')}")
    print(f"Words: {file_data['word_count']}")
    refs = file_data.get('references', [])
    print(f"References: {len(refs)}")
    if refs[:5]:
        for ref in refs[:5]:
            syntax_icon = {'wiki-link': '[[]]', 'hashtag': '#', 'hashtag-bracket': '#[[]]'}.get(ref['syntax'], '?')
            print(f"  {syntax_icon} {ref['target']}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Knowledge Processor")
    parser.add_argument('path', nargs='?', help='File to process')
    parser.add_argument('--scan', action='store_true', help='Scan space for files')
    parser.add_argument('--resolve-links', action='store_true', help='Resolve unresolved links')
    parser.add_argument('--create-stubs', action='store_true', help='Create stubs for unresolved links')
    parser.add_argument('--inject-backlinks', action='store_true', help='Inject backlinks into zettels')
    parser.add_argument('--full-process', action='store_true', help='Run full processing pipeline')
    parser.add_argument('--space', '-s', choices=list(SPACES.keys()), help='Space to operate on')
    parser.add_argument('--no-stubs', action='store_true', help='Skip stub creation in full process')
    parser.add_argument('--no-backlinks', action='store_true', help='Skip backlink injection in full process')

    args = parser.parse_args()

    if args.full_process:
        full_process(
            args.space,
            create_stubs_flag=not args.no_stubs,
            inject_backlinks_flag=not args.no_backlinks
        )

    elif args.scan:
        if not args.space:
            print("Usage: python zettel_processor.py --scan --space SPACE")
            exit(1)
        init_database(args.space)
        scan_space(args.space)
        resolve_links(args.space)

    elif args.resolve_links:
        resolve_links(args.space)

    elif args.create_stubs:
        space = args.space or 'datafund'
        create_stubs_for_unresolved(space)

    elif args.inject_backlinks:
        inject_all_backlinks(args.space)

    elif args.path:
        result = process_file(args.path, args.space)
        if result:
            print_summary(result)

    else:
        parser.print_help()
