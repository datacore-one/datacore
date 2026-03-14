#!/usr/bin/env python3
"""
Add engram injection preamble to agent definition files.

Layer 2 of the 3-layer engram injection system (DIP-0019):
Adds a standard instruction block to agent .md files that tells
agents to call datacore.inject MCP tool at startup.

Usage:
    python add_engram_preamble.py                # Dry run (preview changes)
    python add_engram_preamble.py --apply        # Apply changes
    python add_engram_preamble.py --agent X      # Single agent
    python add_engram_preamble.py --check        # Check which agents lack preamble
"""

import argparse
import os
import re
import sys
from pathlib import Path

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
PREAMBLE_MARKER = "<!-- engram-injection-preamble -->"

PREAMBLE_TEMPLATE = """<!-- engram-injection-preamble -->
### Engram Injection

Before starting work, load relevant learned patterns:

1. **Preferred**: Call `datacore.inject` MCP tool with `prompt` = your task description and `scope` = `agent:{agent_name}`
2. **Fallback**: If MCP is unavailable, read `.datacore/state/agent-engrams/{agent_name}.md` for compiled engrams

Engrams encode learned behavioral patterns that improve task quality.
"""


def find_agent_files():
    """Find all agent .md files."""
    files = {}
    # Core agents
    core_dir = DATACORE_ROOT / ".datacore" / "agents"
    if core_dir.exists():
        for f in core_dir.glob("*.md"):
            files[f.stem] = f

    # Space agents
    for space_dir in sorted(DATACORE_ROOT.glob("[0-9]-*/.datacore/agents")):
        for f in space_dir.glob("*.md"):
            if f.stem not in files:
                files[f.stem] = f

    # Module agents
    modules_dir = DATACORE_ROOT / ".datacore" / "modules"
    if modules_dir.exists():
        for module_dir in modules_dir.iterdir():
            if not module_dir.is_dir():
                continue
            agents_dir = module_dir / "agents"
            if agents_dir.exists():
                for f in agents_dir.glob("*.md"):
                    if f.stem not in files:
                        files[f.stem] = f

    # Filter out non-agent files
    files.pop("README", None)

    return files


def has_preamble(content):
    """Check if file already has the engram injection preamble."""
    return PREAMBLE_MARKER in content


def find_insertion_point(content):
    """Find where to insert the preamble in the agent .md file.

    Insert after the frontmatter and first heading, before ## Agent Context
    or the first section of content.
    """
    lines = content.split("\n")

    # Skip frontmatter (--- ... ---)
    in_frontmatter = False
    frontmatter_end = 0
    for i, line in enumerate(lines):
        if line.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
            else:
                frontmatter_end = i + 1
                break

    # Find ## Agent Context section (insert before it)
    for i in range(frontmatter_end, len(lines)):
        if lines[i].strip().startswith("## Agent Context"):
            return i

    # Find first ## heading after frontmatter
    for i in range(frontmatter_end, len(lines)):
        if lines[i].strip().startswith("## "):
            return i

    # Default: after frontmatter
    return frontmatter_end


def add_preamble(filepath, agent_name, dry_run=True):
    """Add engram injection preamble to an agent file."""
    content = filepath.read_text()

    if has_preamble(content):
        return False, "already has preamble"

    preamble = PREAMBLE_TEMPLATE.replace("{agent_name}", agent_name)
    lines = content.split("\n")
    insert_at = find_insertion_point(content)

    # Insert preamble
    new_lines = lines[:insert_at] + [""] + preamble.strip().split("\n") + [""] + lines[insert_at:]
    new_content = "\n".join(new_lines)

    if not dry_run:
        filepath.write_text(new_content)
        return True, "added"
    return True, "would add"


def main():
    parser = argparse.ArgumentParser(description="Add engram injection preamble to agents")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry run)")
    parser.add_argument("--agent", help="Process specific agent only")
    parser.add_argument("--check", action="store_true", help="Check which agents lack preamble")
    args = parser.parse_args()

    agent_files = find_agent_files()

    if args.agent:
        if args.agent in agent_files:
            agent_files = {args.agent: agent_files[args.agent]}
        else:
            print(f"Agent not found: {args.agent}")
            sys.exit(1)

    if args.check:
        missing = []
        has = []
        for name, filepath in sorted(agent_files.items()):
            content = filepath.read_text()
            if has_preamble(content):
                has.append(name)
            else:
                missing.append(name)
        print(f"With preamble: {len(has)} | Without: {len(missing)}")
        if missing:
            print(f"\nMissing preamble ({len(missing)}):")
            for name in missing:
                print(f"  {name}")
        return

    # Add preamble
    added = 0
    skipped = 0
    for name, filepath in sorted(agent_files.items()):
        changed, status = add_preamble(filepath, name, dry_run=not args.apply)
        if changed:
            added += 1
            if not args.apply:
                print(f"  [DRY RUN] {name}: {status}")
            else:
                print(f"  {name}: {status}")
        else:
            skipped += 1

    action = "Would add" if not args.apply else "Added"
    print(f"\n{action} preamble to {added} agents (skipped {skipped})")
    if not args.apply and added > 0:
        print("Run with --apply to make changes")


if __name__ == "__main__":
    main()
