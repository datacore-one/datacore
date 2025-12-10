#!/usr/bin/env python3
"""
Tag Validator - Validates tags against the canonical registry.

Checks org-mode files and markdown notes for:
- Unknown tags not in registry
- Variant spellings that should be normalized
- Missing required tags

Usage:
    python tag_validator.py                    # Full validation
    python tag_validator.py --org              # Only org files
    python tag_validator.py --notes            # Only markdown notes
    python tag_validator.py --fix              # Auto-fix normalizable variants
    python tag_validator.py --report           # Generate weekly report
"""

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple

import yaml


class TagValidator:
    """Validates tags against the canonical registry."""

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir or os.environ.get("DATA_DIR", os.path.expanduser("~/Data")))
        self.config_dir = self.data_dir / ".datacore"
        self.registry_path = self.config_dir / "config" / "tags.yaml"

        self.registry = {}
        self.known_org_tags: Set[str] = set()
        self.known_hashtags: Set[str] = set()
        self.known_yaml_tags: Set[str] = set()
        self.variants_to_warn: Set[str] = set()
        self.auto_normalize: Dict[str, str] = {}

        self._load_registry()

    def _load_registry(self):
        """Load tag registry from YAML."""
        if not self.registry_path.exists():
            print(f"Warning: Tag registry not found at {self.registry_path}")
            return

        with open(self.registry_path) as f:
            self.registry = yaml.safe_load(f)

        # Extract known tags from all sections
        for section_name, section in self.registry.items():
            if not isinstance(section, dict):
                continue

            # Skip non-tag sections
            if section_name in ('sync_label_mapping', 'validation'):
                continue

            for tag_name, tag_def in section.items():
                if not isinstance(tag_def, dict):
                    continue

                # Collect org tags
                if 'org' in tag_def:
                    self.known_org_tags.add(tag_def['org'])

                # Collect hashtags
                if 'hashtag' in tag_def:
                    self.known_hashtags.add(tag_def['hashtag'])

                # Collect yaml tags
                if 'yaml' in tag_def:
                    self.known_yaml_tags.add(tag_def['yaml'])

                # Collect aliases
                if 'aliases' in tag_def:
                    for alias in tag_def['aliases']:
                        if alias.startswith('#'):
                            self.known_hashtags.add(alias)

        # Load validation rules
        validation = self.registry.get('validation', {})
        self.variants_to_warn = set(validation.get('warn_on_variants', []))
        self.auto_normalize = validation.get('auto_normalize', {})

    def find_org_tags(self, content: str) -> List[str]:
        """Extract org-mode tags from content."""
        # Match :tag: patterns (but not :PROPERTIES:, :END:, etc.)
        tags = re.findall(r':([A-Za-z@][A-Za-z0-9_@-]*(?::[A-Za-z0-9_@-]+)*):(?=\s|$)', content)
        # Also match priority tags
        priorities = re.findall(r'\[#([ABC])\]', content)

        result = [f":{tag}:" for tag in tags]
        result.extend([f"[#{p}]" for p in priorities])
        return result

    def find_hashtags(self, content: str) -> List[str]:
        """Extract hashtags from markdown content."""
        # Match #Tag patterns, excluding hex colors
        tags = re.findall(r'#([A-Za-z][A-Za-z0-9_/-]*)', content)
        # Filter out likely hex colors
        return [f"#{tag}" for tag in tags if not re.match(r'^[0-9A-Fa-f]{3,6}$', tag)]

    def find_yaml_tags(self, content: str) -> List[str]:
        """Extract tags from YAML frontmatter."""
        # Match YAML frontmatter
        match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
        if not match:
            return []

        try:
            frontmatter = yaml.safe_load(match.group(1))
            if frontmatter and 'tags' in frontmatter:
                tags = frontmatter['tags']
                if isinstance(tags, list):
                    return tags
                elif isinstance(tags, str):
                    return [tags]
        except yaml.YAMLError:
            pass

        return []

    def validate_org_files(self) -> Dict[str, List[Tuple[str, int, str]]]:
        """Validate org-mode files. Returns dict of file -> [(tag, line, issue)]."""
        issues = defaultdict(list)

        org_dirs = [
            self.data_dir / "0-personal" / "org",
            self.data_dir / "1-datafund" / "org",
            self.data_dir / "2-datacore" / "org",
        ]

        for org_dir in org_dirs:
            if not org_dir.exists():
                continue

            for org_file in org_dir.glob("*.org"):
                with open(org_file, 'r', errors='ignore') as f:
                    for line_num, line in enumerate(f, 1):
                        tags = self.find_org_tags(line)
                        for tag in tags:
                            # Skip property drawer tags
                            if tag in (':PROPERTIES:', ':END:', ':LOGBOOK:'):
                                continue
                            # Skip common property names (org-mode properties, not tags)
                            if tag.upper() in (':EFFORT:', ':CREATED:', ':ID:', ':CATEGORY:',
                                               ':PRIORITY:', ':SOURCE:', ':STYLE:', ':ARCHIVE:',
                                               ':GOAL:', ':STATUS:', ':DESCRIPTION:', ':URL:',
                                               ':OWNER:', ':PROMPT_REF:', ':LAST_REPEAT:',
                                               ':REPEAT_TO_STATE:', ':PRIORITY_LEVEL:', ':PHASE:',
                                               ':HOME:', ':WORK:', ':DOCUMENT:', ':RELATED:',
                                               ':LEVERAGE:', ':ENTRY:', ':TARGET_AVG:', ':NOTE:',
                                               ':MOVED_TO_SOMEDAY:', ':STRATEGIC_PRIORITY:',
                                               ':WAITING_REASON:', ':WAITING_ON:', ':ASSIGNEE:',
                                               ':DEPENDS:', ':SESSION:', ':DEVELOPMENT_APPROACH:',
                                               ':CANCEL_REASON:', ':DEFERRED_REASON:'):
                                continue

                            if tag in self.variants_to_warn:
                                issues[str(org_file)].append((tag, line_num, f"variant - use {self.auto_normalize.get(tag, '?')}"))
                            elif tag not in self.known_org_tags and not tag.startswith(':[A-Z]'):
                                # Check if it's a custom tag we should track
                                if re.match(r'^:[A-Za-z@]', tag):
                                    issues[str(org_file)].append((tag, line_num, "unknown tag"))

        return dict(issues)

    def validate_notes(self) -> Dict[str, List[Tuple[str, int, str]]]:
        """Validate markdown notes. Returns dict of file -> [(tag, line, issue)]."""
        issues = defaultdict(list)

        notes_dirs = [
            self.data_dir / "0-personal" / "notes",
        ]

        for notes_dir in notes_dirs:
            if not notes_dir.exists():
                continue

            for md_file in notes_dir.rglob("*.md"):
                try:
                    with open(md_file, 'r', errors='ignore') as f:
                        content = f.read()

                    # Check hashtags
                    for line_num, line in enumerate(content.split('\n'), 1):
                        hashtags = self.find_hashtags(line)
                        for tag in hashtags:
                            if tag in self.variants_to_warn:
                                issues[str(md_file)].append((tag, line_num, f"variant - use {self.auto_normalize.get(tag, '?')}"))
                            elif tag not in self.known_hashtags:
                                # Only report if it looks like a real tag (capitalized or common pattern)
                                if tag[1].isupper() or tag.lower() in [t.lower() for t in self.known_hashtags]:
                                    issues[str(md_file)].append((tag, line_num, "unknown hashtag"))

                    # Check YAML frontmatter tags
                    yaml_tags = self.find_yaml_tags(content)
                    for tag in yaml_tags:
                        if tag not in self.known_yaml_tags and tag not in ('stub', 'needs-content'):
                            issues[str(md_file)].append((tag, 1, "unknown yaml tag"))

                except Exception as e:
                    pass  # Skip files that can't be read

        return dict(issues)

    def get_tag_stats(self) -> Dict[str, int]:
        """Get usage statistics for all tags."""
        stats = defaultdict(int)

        # Count org tags
        for org_dir in [self.data_dir / "0-personal" / "org"]:
            if not org_dir.exists():
                continue
            for org_file in org_dir.glob("*.org"):
                with open(org_file, 'r', errors='ignore') as f:
                    content = f.read()
                    for tag in self.find_org_tags(content):
                        stats[f"org:{tag}"] += 1

        # Count hashtags
        for notes_dir in [self.data_dir / "0-personal" / "notes"]:
            if not notes_dir.exists():
                continue
            for md_file in notes_dir.rglob("*.md"):
                try:
                    with open(md_file, 'r', errors='ignore') as f:
                        content = f.read()
                        for tag in self.find_hashtags(content):
                            stats[f"hashtag:{tag}"] += 1
                except:
                    pass

        return dict(stats)

    def generate_report(self) -> str:
        """Generate a validation report."""
        report = []
        report.append("=" * 60)
        report.append("TAG VALIDATION REPORT")
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        report.append("=" * 60)
        report.append("")

        # Registry stats
        report.append("REGISTRY STATS")
        report.append("-" * 40)
        report.append(f"  Org tags known:     {len(self.known_org_tags)}")
        report.append(f"  Hashtags known:     {len(self.known_hashtags)}")
        report.append(f"  YAML tags known:    {len(self.known_yaml_tags)}")
        report.append(f"  Variants to warn:   {len(self.variants_to_warn)}")
        report.append("")

        # Org validation
        report.append("ORG-MODE VALIDATION")
        report.append("-" * 40)
        org_issues = self.validate_org_files()
        if org_issues:
            total_issues = sum(len(v) for v in org_issues.values())
            report.append(f"  Issues found: {total_issues} in {len(org_issues)} files")
            for file_path, issues in sorted(org_issues.items()):
                rel_path = Path(file_path).relative_to(self.data_dir)
                report.append(f"\n  {rel_path}:")
                for tag, line, issue in issues[:10]:  # Limit to 10 per file
                    report.append(f"    Line {line}: {tag} - {issue}")
                if len(issues) > 10:
                    report.append(f"    ... and {len(issues) - 10} more")
        else:
            report.append("  No issues found")
        report.append("")

        # Notes validation
        report.append("NOTES VALIDATION")
        report.append("-" * 40)
        notes_issues = self.validate_notes()
        if notes_issues:
            total_issues = sum(len(v) for v in notes_issues.values())
            report.append(f"  Issues found: {total_issues} in {len(notes_issues)} files")
            # Show top 10 files with most issues
            sorted_files = sorted(notes_issues.items(), key=lambda x: len(x[1]), reverse=True)[:10]
            for file_path, issues in sorted_files:
                rel_path = Path(file_path).relative_to(self.data_dir)
                report.append(f"\n  {rel_path}: {len(issues)} issues")
                for tag, line, issue in issues[:5]:
                    report.append(f"    Line {line}: {tag} - {issue}")
                if len(issues) > 5:
                    report.append(f"    ... and {len(issues) - 5} more")
        else:
            report.append("  No issues found")
        report.append("")

        # New/unknown tags summary
        report.append("NEW TAGS DETECTED")
        report.append("-" * 40)
        all_unknown = set()
        for issues in org_issues.values():
            for tag, _, issue in issues:
                if "unknown" in issue:
                    all_unknown.add(tag)
        for issues in notes_issues.values():
            for tag, _, issue in issues:
                if "unknown" in issue:
                    all_unknown.add(tag)

        if all_unknown:
            report.append(f"  {len(all_unknown)} unknown tags found:")
            for tag in sorted(all_unknown, key=str)[:20]:
                report.append(f"    - {tag}")
            if len(all_unknown) > 20:
                report.append(f"    ... and {len(all_unknown) - 20} more")
            report.append("\n  Consider adding these to config/tags.yaml")
        else:
            report.append("  No new tags detected")

        report.append("")
        report.append("=" * 60)

        return "\n".join(report)

    def fix_variants(self, dry_run: bool = True) -> List[Tuple[str, str, str]]:
        """Fix normalizable variants. Returns list of (file, old, new) changes."""
        changes = []

        for file_path, issues in self.validate_org_files().items():
            for tag, line_num, issue in issues:
                if "variant" in issue and tag in self.auto_normalize:
                    changes.append((file_path, tag, self.auto_normalize[tag]))

        for file_path, issues in self.validate_notes().items():
            for tag, line_num, issue in issues:
                if "variant" in issue and tag in self.auto_normalize:
                    changes.append((file_path, tag, self.auto_normalize[tag]))

        if not dry_run:
            # Group changes by file
            by_file = defaultdict(list)
            for file_path, old, new in changes:
                by_file[file_path].append((old, new))

            for file_path, replacements in by_file.items():
                with open(file_path, 'r') as f:
                    content = f.read()
                for old, new in replacements:
                    content = content.replace(old, new)
                with open(file_path, 'w') as f:
                    f.write(content)

        return changes


def main():
    parser = argparse.ArgumentParser(description="Validate tags against registry")
    parser.add_argument("--org", action="store_true", help="Only validate org files")
    parser.add_argument("--notes", action="store_true", help="Only validate notes")
    parser.add_argument("--fix", action="store_true", help="Auto-fix normalizable variants")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be fixed (with --fix)")
    parser.add_argument("--report", action="store_true", help="Generate full report")
    parser.add_argument("--stats", action="store_true", help="Show tag usage stats")
    parser.add_argument("--data-dir", help="Data directory path")

    args = parser.parse_args()

    validator = TagValidator(args.data_dir)

    if args.report:
        print(validator.generate_report())
        return

    if args.stats:
        stats = validator.get_tag_stats()
        print("TAG USAGE STATISTICS")
        print("-" * 40)
        for tag, count in sorted(stats.items(), key=lambda x: -x[1])[:50]:
            print(f"  {count:4d}  {tag}")
        return

    if args.fix:
        changes = validator.fix_variants(dry_run=args.dry_run)
        if changes:
            print(f"{'Would fix' if args.dry_run else 'Fixed'} {len(changes)} variant tags:")
            for file_path, old, new in changes[:20]:
                print(f"  {old} -> {new} in {Path(file_path).name}")
        else:
            print("No variants to fix")
        return

    # Default: quick validation summary
    org_issues = {} if args.notes else validator.validate_org_files()
    notes_issues = {} if args.org else validator.validate_notes()

    total_org = sum(len(v) for v in org_issues.values())
    total_notes = sum(len(v) for v in notes_issues.values())

    if total_org == 0 and total_notes == 0:
        print("✓ All tags valid")
    else:
        print(f"Tag issues found:")
        if total_org:
            print(f"  Org files: {total_org} issues in {len(org_issues)} files")
        if total_notes:
            print(f"  Notes: {total_notes} issues in {len(notes_issues)} files")
        print("\nRun with --report for details")


if __name__ == "__main__":
    main()
