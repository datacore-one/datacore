#!/usr/bin/env python3
"""
Tag Migration Script (DIP-0014)

Migrates files from frontmatter tag arrays to inline hashtag format.

Before:
```markdown
---
tags: [privacy-tech, blockchain]
---
# Title
Content...
```

After:
```markdown
---
---
# Title
Content...

#privacy-tech, #blockchain
```

Usage:
    python tag_migrator.py scan 0-personal/      # Find files to migrate
    python tag_migrator.py migrate 0-personal/   # Dry-run migration
    python tag_migrator.py migrate 0-personal/ --apply  # Apply changes
"""

import re
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Tuple
import yaml

# Import tag utilities
try:
    from tag_utils import normalize_tag, format_inline_tags
except ImportError:
    # Fallback if not in path
    def normalize_tag(tag: str) -> str:
        """Normalize to kebab-case."""
        tag = tag.lower().strip()
        tag = re.sub(r'[_\s]+', '-', tag)
        tag = re.sub(r'[^a-z0-9-]', '', tag)
        tag = re.sub(r'-+', '-', tag)
        return tag.strip('-')

    def format_inline_tags(tags: List[str]) -> str:
        """Format tags as '#tag1, #tag2'."""
        return ', '.join(f'#{tag}' for tag in tags if tag)


@dataclass
class MigrationResult:
    """Result of migrating a single file."""
    path: Path
    success: bool
    tags_found: List[str]
    tags_migrated: List[str]
    error: Optional[str] = None
    backup_path: Optional[Path] = None


def parse_frontmatter(content: str) -> Tuple[Optional[dict], str, str]:
    """
    Parse YAML frontmatter from markdown content.

    Returns:
        Tuple of (frontmatter_dict, frontmatter_raw, body)
        frontmatter_dict is None if no frontmatter found
    """
    if not content.startswith('---'):
        return None, '', content

    # Find the closing ---
    lines = content.split('\n')
    end_idx = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == '---':
            end_idx = i
            break

    if end_idx is None:
        return None, '', content

    frontmatter_raw = '\n'.join(lines[1:end_idx])
    body = '\n'.join(lines[end_idx + 1:])

    try:
        frontmatter = yaml.safe_load(frontmatter_raw)
        if frontmatter is None:
            frontmatter = {}
    except yaml.YAMLError:
        return None, frontmatter_raw, body

    return frontmatter, frontmatter_raw, body


def extract_array_tags(frontmatter: dict) -> Tuple[List[str], List[str]]:
    """
    Extract tags from frontmatter arrays.

    Returns:
        Tuple of (tags_to_migrate, keys_to_remove)
    """
    tags = []
    keys_to_remove = []

    # Check 'tags' field
    if 'tags' in frontmatter:
        val = frontmatter['tags']
        if isinstance(val, list):
            tags.extend(str(t) for t in val)
            keys_to_remove.append('tags')
        elif isinstance(val, str) and val:
            # Comma-separated string
            tags.extend(t.strip() for t in val.split(','))
            keys_to_remove.append('tags')

    return tags, keys_to_remove


def extract_existing_inline_tags(content: str) -> List[str]:
    """Extract existing inline #tags from content."""
    # Match #tag patterns (not in URLs or code blocks)
    pattern = r'(?<![/\w])#([a-zA-Z][a-zA-Z0-9_-]*)'
    matches = re.findall(pattern, content)
    return [normalize_tag(m) for m in matches]


def rebuild_frontmatter(frontmatter: dict, keys_to_remove: List[str]) -> str:
    """Rebuild frontmatter YAML without specified keys."""
    cleaned = {k: v for k, v in frontmatter.items() if k not in keys_to_remove}

    if not cleaned:
        return ''

    # Custom YAML dump to preserve formatting
    lines = []
    for key, value in cleaned.items():
        if isinstance(value, str):
            if '\n' in value or ':' in value or '"' in value:
                lines.append(f'{key}: "{value}"')
            else:
                lines.append(f'{key}: {value}' if value else f'{key}: ""')
        elif isinstance(value, bool):
            lines.append(f'{key}: {str(value).lower()}')
        elif isinstance(value, (int, float)):
            lines.append(f'{key}: {value}')
        elif isinstance(value, list):
            if not value:
                lines.append(f'{key}: []')
            elif all(isinstance(v, str) for v in value):
                lines.append(f'{key}: [{", ".join(str(v) for v in value)}]')
            else:
                lines.append(yaml.dump({key: value}, default_flow_style=False).strip())
        elif isinstance(value, dict):
            lines.append(yaml.dump({key: value}, default_flow_style=False).strip())
        elif value is None:
            lines.append(f'{key}: ""')
        else:
            lines.append(f'{key}: {value}')

    return '\n'.join(lines)


def migrate_file(path: Path, dry_run: bool = True, create_backup: bool = True) -> MigrationResult:
    """
    Migrate a single file from frontmatter tags to inline format.

    Args:
        path: Path to the markdown file
        dry_run: If True, don't modify files
        create_backup: If True, create .bak file before modifying

    Returns:
        MigrationResult with details of the migration
    """
    try:
        content = path.read_text(encoding='utf-8')
    except Exception as e:
        return MigrationResult(
            path=path,
            success=False,
            tags_found=[],
            tags_migrated=[],
            error=f"Failed to read file: {e}"
        )

    # Parse frontmatter
    frontmatter, fm_raw, body = parse_frontmatter(content)

    if frontmatter is None:
        return MigrationResult(
            path=path,
            success=True,
            tags_found=[],
            tags_migrated=[],
            error="No frontmatter found"
        )

    # Extract array tags
    tags_to_migrate, keys_to_remove = extract_array_tags(frontmatter)

    if not tags_to_migrate:
        return MigrationResult(
            path=path,
            success=True,
            tags_found=[],
            tags_migrated=[],
            error="No array tags found"
        )

    # Normalize tags
    normalized_tags = [normalize_tag(t) for t in tags_to_migrate if t]
    normalized_tags = [t for t in normalized_tags if t]  # Remove empty

    # Check for existing inline tags
    existing_inline = extract_existing_inline_tags(body)

    # Merge and dedupe (preserve order, normalized first)
    all_tags = []
    seen = set()
    for tag in normalized_tags + existing_inline:
        if tag not in seen:
            all_tags.append(tag)
            seen.add(tag)

    # Rebuild content
    new_frontmatter = rebuild_frontmatter(frontmatter, keys_to_remove)

    if new_frontmatter:
        new_content = f"---\n{new_frontmatter}\n---{body}"
    else:
        new_content = f"---\n---{body}"

    # Add inline tags at end (if not already there)
    inline_tag_line = format_inline_tags(all_tags)

    # Check if content ends with tags already
    if not body.rstrip().endswith(tuple(f'#{t}' for t in all_tags)):
        # Add tags at end, ensuring proper spacing
        if new_content.rstrip():
            new_content = new_content.rstrip() + '\n\n' + inline_tag_line + '\n'
        else:
            new_content = new_content + '\n' + inline_tag_line + '\n'

    # Apply changes
    backup_path = None
    if not dry_run:
        if create_backup:
            # Skip backup if filename would be too long (max ~255 chars on most filesystems)
            potential_backup = path.with_suffix(path.suffix + '.bak')
            if len(potential_backup.name) < 250:
                backup_path = potential_backup
                try:
                    backup_path.write_text(content, encoding='utf-8')
                except OSError:
                    backup_path = None  # Skip backup on error

        path.write_text(new_content, encoding='utf-8')

    return MigrationResult(
        path=path,
        success=True,
        tags_found=tags_to_migrate,
        tags_migrated=all_tags,
        backup_path=backup_path
    )


def scan_directory(directory: Path, pattern: str = "*.md") -> List[Path]:
    """Find all markdown files with frontmatter tag arrays."""
    files_to_migrate = []

    for path in directory.rglob(pattern):
        try:
            content = path.read_text(encoding='utf-8')
            frontmatter, _, _ = parse_frontmatter(content)

            if frontmatter and 'tags' in frontmatter:
                val = frontmatter['tags']
                if isinstance(val, list) and val:
                    files_to_migrate.append(path)
                elif isinstance(val, str) and val:
                    files_to_migrate.append(path)
        except Exception:
            continue

    return files_to_migrate


def migrate_directory(
    directory: Path,
    pattern: str = "*.md",
    dry_run: bool = True,
    create_backup: bool = True
) -> List[MigrationResult]:
    """
    Migrate all matching files in a directory.

    Args:
        directory: Directory to scan
        pattern: Glob pattern for files
        dry_run: If True, report only
        create_backup: If True, create backups

    Returns:
        List of MigrationResults
    """
    files = scan_directory(directory, pattern)
    results = []

    for path in files:
        result = migrate_file(path, dry_run=dry_run, create_backup=create_backup)
        results.append(result)

    return results


def main():
    """CLI entry point."""
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    target = Path(sys.argv[2])
    apply_changes = '--apply' in sys.argv

    if not target.exists():
        print(f"Error: {target} does not exist")
        sys.exit(1)

    if command == 'scan':
        # Just find files to migrate
        if target.is_file():
            files = [target] if scan_directory(target.parent, target.name) else []
        else:
            files = scan_directory(target)

        print(f"Found {len(files)} files with frontmatter tag arrays:\n")
        for f in files:
            print(f"  {f}")

    elif command == 'migrate':
        dry_run = not apply_changes

        if target.is_file():
            results = [migrate_file(target, dry_run=dry_run)]
        else:
            results = migrate_directory(target, dry_run=dry_run)

        # Print results
        mode = "DRY RUN" if dry_run else "APPLIED"
        print(f"\n=== Tag Migration ({mode}) ===\n")

        migrated = [r for r in results if r.tags_migrated]
        skipped = [r for r in results if not r.tags_migrated and not r.error]
        errors = [r for r in results if r.error and 'No' not in r.error]

        if migrated:
            print(f"Migrated ({len(migrated)} files):")
            for r in migrated:
                tags_str = ', '.join(r.tags_migrated)
                print(f"  {r.path}")
                print(f"    Tags: {tags_str}")
                if r.backup_path:
                    print(f"    Backup: {r.backup_path}")

        if errors:
            print(f"\nErrors ({len(errors)} files):")
            for r in errors:
                print(f"  {r.path}: {r.error}")

        print(f"\n--- Summary ---")
        print(f"Total scanned: {len(results)}")
        print(f"Migrated: {len(migrated)}")
        print(f"Skipped: {len(skipped)}")
        print(f"Errors: {len(errors)}")

        if dry_run and migrated:
            print(f"\nRun with --apply to apply changes")

    else:
        print(f"Unknown command: {command}")
        print("Commands: scan, migrate")
        sys.exit(1)


if __name__ == '__main__':
    main()
