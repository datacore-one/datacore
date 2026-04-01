#!/usr/bin/env python3
"""Shared triage utilities for input adapter modules (github, mail, etc.).

Provides reusable functions for:
- Creating org-mode tasks from external sources
- Checking nightshift status
- Assessing task complexity
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


def create_triage_task(
    org_file: Path,
    heading: str,
    tags: list[str],
    properties: dict[str, str],
    context_body: str = "",
    scheduled_date: date | None = None,
    state: str = "TODO",
) -> dict:
    """Create an org-mode task in the specified file using org_workspace_adapter.

    Args:
        org_file: Path to next_actions.org
        heading: Task heading text
        tags: List of tags (e.g., ["AI", "github"])
        properties: Dict of properties (GITHUB_URL, COMPLEXITY, etc.)
        context_body: Text body below the task heading
        scheduled_date: Optional scheduled date
        state: Task state (default: TODO)

    Returns:
        dict with keys: success, id, heading, error
    """
    adapter = Path(__file__).parent / "org_workspace_adapter.py"
    if not adapter.exists():
        return {"success": False, "error": f"org_workspace_adapter.py not found at {adapter}"}

    # Check if task with this ID already exists (idempotency)
    task_id = properties.get("ID", "")
    if task_id:
        existing = _find_task_by_id(org_file, task_id)
        if existing:
            return {"success": True, "id": task_id, "heading": heading, "skipped": True}

    # Build tag string for org-mode format
    tag_str = ":".join(tags)

    # Build command
    cmd = [
        sys.executable, str(adapter), "add",
        "--file", str(org_file),
        "--heading", heading,
        "--state", state,
        "--tags", tag_str,
    ]

    if scheduled_date:
        cmd.extend(["--scheduled", scheduled_date.isoformat()])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip()}

        import json
        output = json.loads(result.stdout)
        node_id = output.get("id", "")

        # Now set additional properties by patching the file directly
        if properties:
            _set_task_properties(org_file, node_id or task_id, properties)

        # Append context body if provided
        if context_body:
            _append_task_body(org_file, heading, context_body)

        return {"success": True, "id": node_id or task_id, "heading": heading}

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"success": False, "error": str(e)}


def _find_task_by_id(org_file: Path, task_id: str) -> bool:
    """Check if a task with given :ID: property exists in the file."""
    if not org_file.exists():
        return False
    content = org_file.read_text()
    return f":ID:          {task_id}" in content or f":ID: {task_id}" in content


def _set_task_properties(org_file: Path, node_id: str, properties: dict[str, str]):
    """Set additional properties on a task identified by its node ID.

    Finds the :PROPERTIES: block and inserts new properties before :END:.
    """
    if not org_file.exists():
        return

    lines = org_file.read_text().splitlines()
    new_lines = []
    in_target_props = False
    found_id = False

    for i, line in enumerate(lines):
        if f":ID:" in line and node_id in line:
            found_id = True
            in_target_props = True

        if in_target_props and line.strip() == ":END:":
            for key, value in properties.items():
                if key == "ID":
                    continue
                prop_line = f"   :{key}:{' ' * max(1, 12 - len(key))} {value}"
                new_lines.append(prop_line)
            in_target_props = False

        new_lines.append(line)

    if found_id:
        org_file.write_text("\n".join(new_lines) + "\n")


def _append_task_body(org_file: Path, heading: str, body: str):
    """Append body text after a task's properties block."""
    if not org_file.exists():
        return

    lines = org_file.read_text().splitlines()
    new_lines = []
    found_heading = False
    inserted = False

    for i, line in enumerate(lines):
        new_lines.append(line)

        if not inserted and heading in line and line.strip().startswith("**"):
            found_heading = True

        if found_heading and not inserted and line.strip() == ":END:":
            for body_line in body.strip().splitlines():
                new_lines.append(f"   {body_line}")
            inserted = True

    if inserted:
        org_file.write_text("\n".join(new_lines) + "\n")


def check_nightshift_ran(data_dir: Path, check_date: date | None = None) -> bool:
    """Check if nightshift produced output files for the given date.

    Looks for nightshift-summary-* or nightshift-* files in 0-personal/0-inbox/.
    """
    if check_date is None:
        check_date = date.today()

    yesterday = check_date - timedelta(days=1)
    inbox = data_dir / "0-personal" / "0-inbox"
    if not inbox.exists():
        return False

    for d in [yesterday, check_date]:
        pattern = f"nightshift-*{d.isoformat()}*"
        if list(inbox.glob(pattern)):
            return True

    return False


def assess_complexity(
    estimated_lines: int = 0,
    files_affected: int = 1,
    paths_touched: list[str] = None,
    protected_patterns: list[str] = None,
    max_lines: int = 50,
) -> dict:
    """Assess whether a change is simple or complex.

    Returns dict with: complexity ("simple"|"complex"), reasons (list of strings).
    """
    paths_touched = paths_touched or []
    protected_patterns = protected_patterns or [
        "*.test.*", "*.spec.*", ".github/**", "**/security*", "**/auth*",
    ]

    reasons = []

    if estimated_lines >= max_lines:
        reasons.append(f"Change too large: {estimated_lines} lines >= {max_lines} limit")

    if files_affected >= 3:
        reasons.append(f"Too many files: {files_affected} >= 3")

    import fnmatch
    for path in paths_touched:
        for pattern in protected_patterns:
            if fnmatch.fnmatch(path, pattern):
                reasons.append(f"Protected path: {path} matches {pattern}")
                break

    complexity = "complex" if reasons else "simple"
    return {"complexity": complexity, "reasons": reasons}
