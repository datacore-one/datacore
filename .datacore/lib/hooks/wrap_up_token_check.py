#!/usr/bin/env python3
"""PreToolUse hook for plur_session_end: verify Token Cost was written to journal.

Blocks plur_session_end if neither today's nor yesterday's journal contains a
"Token Cost" section, which means the wrap-up skipped the token accounting step.

Scans BOTH today and yesterday so sessions that roll past midnight (with all
content kept on the session-start date) are not falsely blocked. This mirrors
wrap_up_checklist_check.py which has the same window — and since that hook
also enforces Token Cost as one of three required sections, this hook is
effectively a narrower subset. Kept for legacy compatibility.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path


def journal_paths_recent() -> list[Path]:
    today = date.today()
    yesterday = today - timedelta(days=1)
    paths = []
    for d in (today, yesterday):
        iso = d.isoformat()
        paths.append(Path.home() / "Data" / "0-personal" / "notes" / "journals" / f"{iso}.md")
        paths.append(Path.home() / "Data" / "0-personal" / "journal" / f"{iso}.md")
    return paths


def main():
    raw = sys.stdin.read(1024 * 1024)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        sys.stdout.write(raw)
        return

    if data.get("tool_name", "") != "mcp__plur__plur_session_end":
        sys.stdout.write(raw)
        return

    # Scan today + yesterday — accept first match
    for jp in journal_paths_recent():
        if not jp.exists():
            continue
        try:
            content = jp.read_text()
        except Exception:
            continue
        if "token cost" in content.lower():
            sys.stdout.write(raw)
            return

    # Token cost section missing in both — block with feedback
    sys.stdout.write(json.dumps({
        "decision": "block",
        "reason": (
            "WRAP-UP INCOMPLETE: Neither today's nor yesterday's personal journal has a "
            "'Token Cost' section. Sessions that roll past midnight keep all wrap-up content "
            "on the session-start date — but it must exist on one of the two. Write the "
            "token cost table to the journal BEFORE calling plur_session_end. Calculate "
            "subagent tokens from their usage blocks, estimate main conversation, show total."
        ),
    }))


if __name__ == "__main__":
    main()
