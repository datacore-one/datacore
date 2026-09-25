#!/usr/bin/env python3
"""Shared triage utilities for input adapter modules (github, mail, etc.).

Provides reusable functions for:
- Creating org-mode tasks from external sources
- Checking nightshift status
- Assessing task complexity

The two helpers that patch a task after the adapter created it
(`_set_task_properties`, `_append_task_body`) write inside
`org_transaction.serialized`: they take the org transaction lock, re-read the
file under it (`watch_file`), and write atomically through `write_org_text`
(decision G8, 2026-09-23). Before that they used a plain unlocked
`write_text`, so an adapter commit landing between their read and write was
lost. Each helper is its own transaction: `create_triage_task` runs the
adapter as a subprocess that takes the same lock, so the lock must not be held
across that call.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import org_transaction


def _read_locked(org_file: Path) -> str | None:
    """Watch and read `org_file` inside the current org transaction."""
    org_transaction.watch_file(org_file)
    return org_transaction.read_text(Path(org_file).resolve())


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

    # Check if task with this TRIAGE_ID already exists (idempotency)
    # We use TRIAGE_ID instead of ID because org_workspace manages :ID: internally
    task_id = properties.get("TRIAGE_ID", "")
    if task_id:
        # New tasks are captured into inbox.org and the inbox processor moves them on,
        # so "already triaged" means found in EITHER file -- or the item is re-captured daily.
        org_file = Path(org_file)
        for f in dict.fromkeys([org_file, org_file.parent / "inbox.org", org_file.parent / "next_actions.org"]):
            if f.exists() and _find_task_by_id(f, task_id):
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
            # The adapter reports refusals as JSON on stdout; stderr alone made the
            # error empty ("Failed to create task for datacore-mcp#19: ", 2026-09-25).
            why = result.stderr.strip() or result.stdout.strip() or f"adapter exit {result.returncode}"
            return {"success": False, "error": why[:300]}

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
    """Check if a task with given :TRIAGE_ID: property exists in the file.

    Matches the id exactly, and also matches LEGACY date-suffixed ids of the
    form ``<task_id>-YYYY-MM-DD``. Triage ids used to carry the creation date,
    so the same GitHub issue produced a different id every day and never
    matched its own existing task — on 2026-08-10 that appended a second copy
    of the body to an already-DONE entry from 2026-07-08. New ids are identity
    only; this keeps the tasks written under the old scheme deduplicating.

    The previous implementation was ``task_id in content``, a substring test
    over the WHOLE file: it would also match a longer id that merely contained
    this one, and matched text anywhere, not a TRIAGE_ID line.
    """
    if not org_file.exists():
        return False
    pattern = re.compile(
        r"^\s*:TRIAGE_ID:\s*" + re.escape(task_id) + r"(-\d{4}-\d{2}-\d{2})?\s*$",
        re.M,
    )
    return bool(pattern.search(org_file.read_text()))


@org_transaction.serialized
def _set_task_properties(org_file: Path, node_id: str, properties: dict[str, str]):
    """Set additional properties on a task identified by its node ID.

    Finds the :PROPERTIES: block and inserts new properties before :END:.
    Runs under the org transaction lock and writes atomically (G8).
    """
    if not org_file.exists():
        return

    content = _read_locked(org_file)
    if content is None:
        return
    lines = content.splitlines()
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
        org_transaction.write_org_text(org_file, "\n".join(new_lines) + "\n")


#: States whose tasks are finished. Appending fresh triage context to one of
#: these is always wrong — the work is done and the entry is a record.
#: DEFERRED is benched rather than finished, but it is skipped too (decision
#: G11, 2026-09-23): fresh triage context goes to an open task, never to one
#: the owner parked.
_CLOSED_STATES = ("DONE", "CANCELLED", "CANCELED", "DEFERRED")


@org_transaction.serialized
def _append_task_body(org_file: Path, heading: str, body: str):
    """Append body text after a task's properties block.

    Guards added 2026-08-10 after this duplicated a body block into a DONE task:

    * it matches on HEADING, and matches the FIRST heading containing that text
      — which is an old task from a previous cycle whenever the heading repeats;
    * it appended unconditionally, so re-running produced N copies.

    Now: closed (and DEFERRED) tasks are never appended to, and a body already present is not
    written twice. Both make the function idempotent, which is what the caller
    already assumed it was.

    Both are scoped to the TARGET task's own section (its heading up to the
    next heading), found 2026-09-23 by the Lean model: the insertion point was
    "the first `:END:` after the heading" with no boundary, so a target without
    a drawer put the body into the NEXT task's drawer; and the "already
    recorded" test searched the whole file, so a first line another task
    already carried (e.g. "Source: github") suppressed the write here.

    Runs under the org transaction lock and writes atomically (G8).
    """
    if not org_file.exists():
        return

    content = _read_locked(org_file)
    if content is None:
        return
    lines = content.splitlines()
    body_lines = body.strip().splitlines()
    first_body_line = body_lines[0].strip() if body_lines else ""
    if not body_lines:
        return

    is_heading = re.compile(r"^\*+\s")
    for h, line in enumerate(lines):
        if not (heading in line and line.strip().startswith("**")):
            continue
        # Never append triage context to a finished task.
        after_stars = line.strip().lstrip("*").strip()
        if any(after_stars.startswith(s) for s in _CLOSED_STATES):
            continue
        section_end = next((k for k in range(h + 1, len(lines))
                            if is_heading.match(lines[k])), len(lines))
        section = lines[h + 1:section_end]
        end = next((k for k, l in enumerate(section) if l.strip() == ":END:"), None)
        if end is None:
            continue  # no drawer of its own: try the next matching heading
        # Already recorded in THIS task -- do not write a second copy.
        if any(first_body_line in l for l in section):
            return
        at = h + 1 + end + 1
        new_lines = lines[:at] + [f"   {b}" for b in body_lines] + lines[at:]
        org_transaction.write_org_text(org_file, "\n".join(new_lines) + "\n")
        return


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
