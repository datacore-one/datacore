#!/usr/bin/env python3
"""Apply Datacore focus mode context to project CLAUDE.md files.

Scans all projects in [space]/2-projects/ and either:
- Appends the Datacore section to existing CLAUDE.md
- Creates a minimal CLAUDE.md with the Datacore section

Usage:
    python3 .datacore/lib/apply_focus_context.py --dry-run    # show what would change
    python3 .datacore/lib/apply_focus_context.py --apply       # apply changes
    python3 .datacore/lib/apply_focus_context.py --space 1-datafund --apply  # single space
"""

import argparse
import os
import re
import sys
from pathlib import Path

MARKER = "## Datacore Space Context"

SECTION_TEMPLATE = """
## Datacore Space Context

This project lives inside a Datacore space. Session lifecycle commands are available:

- `/wrap-up` — write session entry to team journal, commit and push
- `/continue` — resume from yesterday's continuation notes; `--save` persists current work
- `/standup` — generate/post standup from recent team journals
- `/today` — daily briefing (incremental if already generated)

| Key | Value |
|-----|-------|
| Space | `{space_dir}` |
| Journal | `~/Data/{space_dir}/journal/YYYY-MM-DD.md` |
| Org | `~/Data/{space_dir}/org/next_actions.org` |

When `/wrap-up` runs, use the team journal schema: `## @contributor` narrative sections + `## Session Metadata` YAML block.
"""

MINIMAL_TEMPLATE = """# CLAUDE.md

## {project_name}

> TODO: Add project description, development setup, and key files.

{section}"""


def find_datacore_root() -> Path:
    """Find ~/Data/ root."""
    root = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
    if not (root / ".datacore").is_dir():
        print(f"Error: {root} is not a Datacore root", file=sys.stderr)
        sys.exit(1)
    return root


def find_projects(root: Path, space_filter: str | None = None) -> list[dict]:
    """Find all project directories across spaces."""
    projects = []
    for space_dir in sorted(root.iterdir()):
        if not re.match(r"^\d+-", space_dir.name):
            continue
        if space_filter and space_dir.name != space_filter:
            continue

        projects_dir = space_dir / "2-projects"
        if not projects_dir.is_dir():
            continue

        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            if project_dir.name.startswith("."):
                continue

            claude_md = project_dir / "CLAUDE.md"
            projects.append({
                "space_dir": space_dir.name,
                "project_dir": project_dir,
                "project_name": project_dir.name,
                "claude_md": claude_md,
                "has_claude_md": claude_md.exists(),
                "has_section": claude_md.exists() and MARKER in claude_md.read_text(),
            })
    return projects


def generate_section(space_dir: str) -> str:
    return SECTION_TEMPLATE.format(space_dir=space_dir).strip()


def apply_to_project(project: dict, dry_run: bool) -> str:
    """Apply focus context to a single project. Returns status string."""
    claude_md = project["claude_md"]
    section = generate_section(project["space_dir"])

    if project["has_section"]:
        return "SKIP (already has Datacore section)"

    if project["has_claude_md"]:
        if dry_run:
            return "WOULD APPEND Datacore section"
        content = claude_md.read_text()
        claude_md.write_text(content.rstrip() + "\n\n" + section + "\n")
        return "APPENDED Datacore section"
    else:
        if dry_run:
            return "WOULD CREATE minimal CLAUDE.md"
        content = MINIMAL_TEMPLATE.format(
            project_name=project["project_name"],
            section=section,
        ).lstrip()
        claude_md.write_text(content)
        return "CREATED minimal CLAUDE.md"


def main():
    parser = argparse.ArgumentParser(description="Apply Datacore focus context to projects")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change")
    parser.add_argument("--apply", action="store_true", help="Apply changes")
    parser.add_argument("--space", help="Filter to single space (e.g., 1-datafund)")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("Specify --dry-run or --apply", file=sys.stderr)
        sys.exit(1)

    root = find_datacore_root()
    projects = find_projects(root, args.space)

    if not projects:
        print("No projects found.")
        return

    for p in projects:
        status = apply_to_project(p, dry_run=args.dry_run)
        print(f"  {p['space_dir']}/{p['project_name']}: {status}")

    # Summary
    total = len(projects)
    has_section = sum(1 for p in projects if p["has_section"])
    needs_append = sum(1 for p in projects if p["has_claude_md"] and not p["has_section"])
    needs_create = sum(1 for p in projects if not p["has_claude_md"])

    print(f"\n  Total: {total} | Already done: {has_section} | Append: {needs_append} | Create: {needs_create}")


if __name__ == "__main__":
    main()
