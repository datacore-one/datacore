#!/usr/bin/env python3
"""Focus mode detection for Datacore.

Detects when CWD is inside a space's 2-projects/ directory and returns
space context for session lifecycle commands.

Usage:
    python3 .datacore/lib/focus_mode.py detect
    python3 .datacore/lib/focus_mode.py shim
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def find_datacore_root() -> Path | None:
    """Walk up from CWD to find ~/Data/ (directory containing .datacore/).

    A valid Datacore root has .datacore/registry/ (agents.yaml, commands.yaml).
    This distinguishes the real Data root from spaces (which have .datacore/ but
    no registry) and app-state directories like ~/.datacore/.
    """
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        dc = parent / ".datacore"
        if dc.is_dir() and (dc / "registry").is_dir():
            return parent
    return None


def find_parent_space(datacore_root: Path) -> dict | None:
    """Check if CWD is inside a space's 2-projects/ directory.

    Returns dict with space info or None if not in focus mode.
    """
    cwd = Path.cwd().resolve()
    rel = None
    try:
        rel = cwd.relative_to(datacore_root)
    except ValueError:
        return None

    parts = rel.parts
    if len(parts) < 2:
        return None

    # First part must be a numbered space directory: [0-9]-*
    space_dir = parts[0]
    if not re.match(r"^\d+-", space_dir):
        return None

    # Second part must be 2-projects
    if parts[1] != "2-projects":
        return None

    # Extract project name (third part, if present)
    project = parts[2] if len(parts) >= 3 else None

    space_path = datacore_root / space_dir
    space_name = re.sub(r"^\d+-", "", space_dir)

    return {
        "mode": "focus",
        "space_dir": space_dir,
        "space_name": space_name,
        "space_path": str(space_path),
        "project": project,
        "journal_path": str(space_path / "journal"),
        "org_path": str(space_path / "org"),
        "datacore_root": str(datacore_root),
    }


def get_contributor() -> str:
    """Get contributor name from git config."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def detect() -> dict:
    """Main detection: returns mode info as JSON."""
    root = find_datacore_root()
    if root is None:
        return {"mode": "none", "reason": "no .datacore/ found in parents"}

    cwd = Path.cwd().resolve()
    if cwd == root:
        return {"mode": "full", "datacore_root": str(root)}

    space_info = find_parent_space(root)
    if space_info is None:
        # Inside a space but not in 2-projects/ — full mode
        return {"mode": "full", "datacore_root": str(root)}

    space_info["contributor"] = get_contributor()
    return space_info


def generate_shim(info: dict) -> str:
    """Generate the minimal focus mode context shim."""
    return f"""# Datacore Focus Mode

Space: {info['space_dir']} ({info['space_path']}/)
Project: {info.get('project', 'unknown')}
Contributor: {info['contributor']}
Journal: {info['journal_path']}/YYYY-MM-DD.md
Org: {info['org_path']}/next_actions.org

## Available Commands
- /wrap-up — write session entry to space journal, commit and push
- /continue — read continuation notes from yesterday's journal; --save persists as task
- /standup — generate/post standup from recent journals
- /today — open daily briefing (incremental if already generated)

## Journal Entry Schema

When writing team journal entries, use this format:

Frontmatter:
  type: team-journal
  date: YYYY-MM-DD
  space: {info['space_name']}
  contributors: [{info['contributor']}]

Sections (in order):
  ## Standup — checkbox items with org task ID comments
  ## @contributor — narrative with project, decisions, continuation
  ## Session Metadata — YAML block with artifacts, git refs, tokens
"""


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: focus_mode.py detect|shim", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "detect":
        print(json.dumps(detect(), indent=2))
    elif cmd == "shim":
        info = detect()
        if info["mode"] == "focus":
            print(generate_shim(info))
        else:
            print(json.dumps(info, indent=2))
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
