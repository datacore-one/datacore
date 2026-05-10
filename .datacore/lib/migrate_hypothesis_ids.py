#!/usr/bin/env python3
"""migrate_hypothesis_ids.py — Add <venture>-prefix to hypothesis IDs across ventures.

Mechanical, idempotent. Adds:
  - id: H001  →  id: fds-H001  (with aliases: ["H001"])
  - requirements: [] field to each hypothesis (empty default)

Preserves YAML formatting and comments via line-based regex editing.
Skips hypotheses that are already migrated (id contains a hyphen).

Usage:
  python3 migrate_hypothesis_ids.py [--dry-run] [--venture=NAME]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


# Map space-dir name → venture short name used as prefix
VENTURE_PREFIX = {
    "1-datafund": "datafund",
    "2-datacore": "datacore",
    "3-fds": "fds",
    "4-forge": "forge",
    "5-plur": "plur",
    "6-meridian": "meridian",
    "7-megaphone": "megaphone",
}


def migrate_file(path: Path, prefix: str, dry_run: bool = False) -> dict:
    """Migrate one hypotheses.yaml file. Returns stats."""
    text = path.read_text()
    lines = text.split("\n")
    new_lines = []

    stats = {
        "renamed": 0,
        "skipped_already_migrated": 0,
        "requirements_added": 0,
    }

    # Walk line-by-line. We rewrite ONLY top-level hypothesis IDs that look like
    # H<digits> (e.g. H001, H42). Sub-experiment IDs (E1, A, B, session_call_site,
    # ...) and already-prefixed IDs (fds-H001) are skipped.
    id_pattern = re.compile(r'^(\s*)- id:\s*"?(H\d+)"?\s*$')

    i = 0
    while i < len(lines):
        line = lines[i]
        m = id_pattern.match(line)
        if not m:
            new_lines.append(line)
            i += 1
            continue

        indent_dash = m.group(1)  # whitespace before the dash
        old_id = m.group(2)

        # Already migrated? (contains a hyphen and matches a known prefix)
        if "-" in old_id and old_id.startswith(prefix + "-"):
            stats["skipped_already_migrated"] += 1
            new_lines.append(line)
            i += 1
            continue
        # Already prefixed with some OTHER venture? Don't touch (unusual).
        if "-" in old_id and not old_id.startswith(prefix + "-"):
            new_lines.append(line)
            i += 1
            continue

        # Property indent: dash plus "  " is the property level for list items
        prop_indent = indent_dash + "  "

        new_id = f"{prefix}-{old_id}"
        new_lines.append(f'{indent_dash}- id: {new_id}')
        # Inject alias + requirements right after id (so they read together).
        new_lines.append(f'{prop_indent}aliases: ["{old_id}"]')

        # Look ahead — does this hypothesis already have a `requirements:` field?
        # Scan forward until next sibling (line at same or shallower indent that
        # starts a new list item or a new top-level mapping key).
        j = i + 1
        has_requirements = False
        while j < len(lines):
            peek = lines[j]
            # Stop at next list item at the same indent level
            if peek.startswith(indent_dash + "- "):
                break
            # Stop at de-indent below dash
            if peek.strip() and len(peek) - len(peek.lstrip()) < len(indent_dash):
                break
            if peek.lstrip().startswith("requirements:"):
                has_requirements = True
                break
            j += 1

        if not has_requirements:
            new_lines.append(f'{prop_indent}requirements: []')
            stats["requirements_added"] += 1

        stats["renamed"] += 1
        i += 1

    new_text = "\n".join(new_lines)
    if new_text == text:
        return stats

    if not dry_run:
        path.write_text(new_text)
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--venture", default=None)
    args = parser.parse_args()

    data = Path.home() / "Data"
    total = {"renamed": 0, "skipped_already_migrated": 0, "requirements_added": 0}

    for space_name, prefix in VENTURE_PREFIX.items():
        if args.venture and args.venture != space_name and args.venture != prefix:
            continue
        path = data / space_name / "hypotheses.yaml"
        if not path.exists():
            print(f"  {space_name}: no hypotheses.yaml")
            continue
        stats = migrate_file(path, prefix, dry_run=args.dry_run)
        for k in total:
            total[k] += stats[k]
        print(f"  {space_name} (prefix={prefix}): renamed={stats['renamed']}, "
              f"already_migrated={stats['skipped_already_migrated']}, "
              f"requirements_added={stats['requirements_added']}")

    print(f"\nTOTAL: renamed={total['renamed']}, "
          f"already_migrated={total['skipped_already_migrated']}, "
          f"requirements_added={total['requirements_added']}")
    if args.dry_run:
        print("(dry-run — no files modified)")


if __name__ == "__main__":
    main()
