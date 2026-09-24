#!/usr/bin/env python3
"""Apply Datacore focus mode context to each project's agent instruction files.

Scans all projects in [space]/2-projects/ and, for CLAUDE.md (Claude Code) and
AGENTS.md (Codex, Cursor, Antigravity, OpenCode, OpenClaw), either:
- Appends the Datacore section to the existing file
- Creates a minimal file with the Datacore section

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

In a harness without these slash commands, call the `datacore_command_run` MCP
tool with the command name (e.g. `wrap-up`) and follow the steps it returns.

| Key | Value |
|-----|-------|
| Space | `{space_dir}` |
| Journal | `~/Data/{space_dir}/journal/YYYY-MM-DD.md` |
| Org | `~/Data/{space_dir}/org/next_actions.org` |

When `/wrap-up` runs, use the team journal schema: `## @contributor` narrative sections + `## Session Metadata` YAML block.
"""

# Every harness reads one of these. Same section in each, so a project gets
# Datacore context whichever agent opens it.
CONTEXT_FILES = ("CLAUDE.md", "AGENTS.md")

# An AGENTS.md created beside a real CLAUDE.md points at it rather than
# stubbing a second, empty project description.
POINTER_TEMPLATE = """# AGENTS.md

Project guidance for this repository is in `CLAUDE.md` — read it before working.

{section}"""

MINIMAL_TEMPLATE = """# {file_name}

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

            files = {}
            for name in CONTEXT_FILES:
                path = project_dir / name
                files[name] = {
                    "path": path,
                    "exists": path.exists(),
                    "has_section": path.exists() and MARKER in path.read_text(),
                }
            projects.append({
                "space_dir": space_dir.name,
                "project_dir": project_dir,
                "project_name": project_dir.name,
                "files": files,
            })
    return projects


def generate_section(space_dir: str) -> str:
    return SECTION_TEMPLATE.format(space_dir=space_dir).strip()


def apply_to_project(project: dict, dry_run: bool) -> str:
    """Apply focus context to one project's instruction files. Returns a status string."""
    section = generate_section(project["space_dir"])
    claude_exists = project["files"]["CLAUDE.md"]["exists"]
    statuses = []
    for name, f in project["files"].items():
        if f["has_section"]:
            statuses.append(f"{name}: skip")
            continue
        if f["exists"]:
            if not dry_run:
                f["path"].write_text(f["path"].read_text().rstrip() + "\n\n" + section + "\n")
            statuses.append(f"{name}: {'would append' if dry_run else 'appended'}")
            continue
        if name == "AGENTS.md" and claude_exists:
            content = POINTER_TEMPLATE.format(section=section)
        else:
            content = MINIMAL_TEMPLATE.format(
                file_name=name, project_name=project["project_name"], section=section,
            ).lstrip()
        if not dry_run:
            f["path"].write_text(content)
        statuses.append(f"{name}: {'would create' if dry_run else 'created'}")
    return ", ".join(statuses)


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
    files = [f for p in projects for f in p["files"].values()]
    done = sum(1 for f in files if f["has_section"])
    append = sum(1 for f in files if f["exists"] and not f["has_section"])
    create = sum(1 for f in files if not f["exists"])
    print(f"\n  Projects: {total} | Files already done: {done} | Append: {append} | Create: {create}")


if __name__ == "__main__":
    main()
