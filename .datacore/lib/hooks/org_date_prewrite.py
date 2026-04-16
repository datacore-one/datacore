#!/usr/bin/env python3
"""PreToolUse hook: reject Edit/Write to .org/.md containing wrong day-of-week.

LLMs often hallucinate day-of-week names (e.g. "2026-04-08 Tue" when it's Wed).
This hook inspects the payload BEFORE the write lands, catching mistakes early
instead of relying on post-hoc cleanup.

Exit codes:
  0 = allow (no mismatch, or not a markdown/org file)
  2 = block (one or more wrong day-of-week names found)

Only scans the NEW content (new_string / content), not the old content — we
don't want to block legitimate edits that leave a pre-existing mismatch intact.
"""
import json
import os
import sys

# Add parent dir to path so we can import date_utils
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from date_utils import find_mismatches  # noqa: E402


def main() -> None:
    raw = sys.stdin.read(2 * 1024 * 1024)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "") or tool_input.get("file", "")
    if not file_path or not file_path.endswith((".org", ".md")):
        sys.stdout.write(raw)
        sys.exit(0)

    # Gather new content from Edit or Write
    new_text = ""
    if "new_string" in tool_input:
        new_text = tool_input.get("new_string", "") or ""
    elif "content" in tool_input:
        new_text = tool_input.get("content", "") or ""
    elif "edits" in tool_input:  # MultiEdit
        for edit in tool_input.get("edits", []):
            new_text += "\n" + (edit.get("new_string") or "")

    if not new_text:
        sys.stdout.write(raw)
        sys.exit(0)

    mismatches = find_mismatches(new_text)
    if not mismatches:
        sys.stdout.write(raw)
        sys.exit(0)

    lines = [
        f"BLOCKED: Wrong day-of-week name(s) in write to {os.path.basename(file_path)}:",
    ]
    for mm in mismatches[:10]:
        lines.append(
            f"  {mm['date']} claimed {mm['claimed']}, actual is {mm['actual']}"
        )
    if len(mismatches) > 10:
        lines.append(f"  ... and {len(mismatches) - 10} more")
    lines.append(
        "Use .datacore/lib/date_utils.py (or the datacore.date MCP tool) to get "
        "correct day names. Never type day-of-week from memory."
    )
    sys.stderr.write("\n".join(lines) + "\n")
    sys.exit(2)


if __name__ == "__main__":
    main()
