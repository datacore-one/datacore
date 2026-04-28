#!/usr/bin/env python3
"""Recover deleted org tasks from a space's knowledge.db index.

The knowledge index caches structured task metadata (heading, state, priority,
tags, scheduled, properties, level, line_number) but NOT body content.

This script reconstructs an org file from indexed metadata. Body content cannot
be recovered — only the task scaffold (heading + properties).

Usage:
    python3 recover_org_from_index.py <space_path> <source_file_pattern> <output_path>

Example:
    python3 recover_org_from_index.py 2-datacore '%next_actions%' /tmp/recovered.org
"""
from __future__ import annotations
import sys
import sqlite3
import ast
from pathlib import Path


def render_task(row: dict) -> str:
    """Render a task row as org-mode heading + properties."""
    level = row["level"] or 2
    stars = "*" * level
    state = row["state"]
    priority = f"[#{row['priority']}] " if row["priority"] else ""
    heading = row["heading"]
    tags = row["tags"] or ""

    # tag string already includes leading/trailing colons (e.g., ":foo:bar:")
    tag_suffix = f"  {tags}" if tags else ""

    out = [f"{stars} {state} {priority}{heading}{tag_suffix}"]

    # Properties — stored as Python repr dict (single quotes)
    props_str = row["properties"]
    if props_str and props_str.strip() and props_str != "{}":
        try:
            props = ast.literal_eval(props_str)
        except (ValueError, SyntaxError):
            props = {}
        if props:
            out.append("  :PROPERTIES:")
            for k, v in props.items():
                # CONTEXT/etc may store '|' marker for multiline, skip empty values
                if v in (None, "", "|"):
                    continue
                out.append(f"  :{k}: {v}")
            out.append("  :END:")

    if row["scheduled"]:
        out.append(f"  SCHEDULED: <{row['scheduled']}>")
    if row["deadline"]:
        out.append(f"  DEADLINE: <{row['deadline']}>")

    out.append("  # NOTE: body content lost in deletion — recovered from index only")
    return "\n".join(out)


def recover(space_path: Path, file_pattern: str, output: Path) -> None:
    db = space_path / ".datacore" / "knowledge.db"
    if not db.exists():
        sys.exit(f"knowledge.db not found at {db}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT state, heading, priority, tags, scheduled, deadline,
               level, properties, source_file, line_number, org_id
        FROM tasks
        WHERE source_file LIKE ?
        ORDER BY source_file, line_number
        """,
        (file_pattern,),
    ).fetchall()
    conn.close()

    if not rows:
        sys.exit(f"No tasks matched pattern {file_pattern!r}")

    by_file: dict[str, list[dict]] = {}
    for r in rows:
        by_file.setdefault(r["source_file"], []).append(dict(r))

    blocks: list[str] = []
    for src, tasks in by_file.items():
        blocks.append(f"# RECOVERED FROM INDEX — original source: {src}")
        blocks.append(f"# Tasks recovered: {len(tasks)} (body content not indexed, lost)")
        blocks.append("")
        for t in tasks:
            blocks.append(render_task(t))
            blocks.append("")
        blocks.append("")

    output.write_text("\n".join(blocks))
    print(f"Recovered {sum(len(v) for v in by_file.values())} tasks → {output}")
    for src, tasks in by_file.items():
        print(f"  {src}: {len(tasks)} tasks")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit("usage: recover_org_from_index.py <space_path> <source_file_pattern> <output_path>")
    recover(Path(sys.argv[1]).resolve(), sys.argv[2], Path(sys.argv[3]))
