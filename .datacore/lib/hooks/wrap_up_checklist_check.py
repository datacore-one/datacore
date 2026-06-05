#!/usr/bin/env python3
"""PreToolUse hook for plur_session_end: enforce wrap-up checklist completion.

Blocks plur_session_end (and by extension, session close) if today's personal
journal is missing the explicit "Wrap-up Checklist Audit" section that
/wrap-up step 18 produces. That section is the user-visible proof that
every spec step was either run or explicitly justified-as-skipped.

This is the structural defense against silent step-skipping. Memory alone
(engrams about "do not skip") has been insufficient — see ENG-2026-0512-044
which already documented this failure mode 17 days before it recurred at
SMK 2026 wrap-up. The hook makes the skip mechanically impossible.

Required journal sections (case-insensitive substring match):
  - "Wrap-up Checklist Audit"
  - "Token Cost"
  - "Session Meta-Analysis"

Allowed status values in the audit table (the hook does NOT enforce these
syntactically — it relies on the spec at .datacore/commands/wrap-up.md §18
for validation, but agents should use these statuses when writing the audit):
  - `run ✓` — step executed
  - `skipped-by-user` — user explicitly declined via prompt
  - `skipped-by-mode-fast` — suppressed by `/wrap-up fast`
  - `not-applicable (REASON)` — concrete factual reason
  - `inferred-and-reported (DESCRIPTION)` — inference-first mode default
  - `applied-from-feedback (N CORRECTIONS)` — §17.5 feedback gate applied edits

If any required section is missing, the hook outputs a `decision: block` JSON
response on stdout, which Claude Code surfaces to the model and refuses the
tool call. The model must then complete the missing section(s) before retrying.

Pass-through otherwise.
"""
import json
import sys
from datetime import date
from pathlib import Path


REQUIRED_SECTIONS = (
    "Wrap-up Checklist Audit",
    "Token Cost",
    "Session Meta-Analysis",
)


def journal_paths_today() -> list[Path]:
    today = date.today().isoformat()
    return [
        Path.home() / "Data" / "0-personal" / "notes" / "journals" / f"{today}.md",
        Path.home() / "Data" / "0-personal" / "journal" / f"{today}.md",
    ]


def find_missing_sections(content: str) -> list[str]:
    lc = content.lower()
    return [s for s in REQUIRED_SECTIONS if s.lower() not in lc]


def main() -> None:
    raw = sys.stdin.read(1024 * 1024)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        sys.stdout.write(raw)
        return

    if data.get("tool_name", "") != "mcp__plur__plur_session_end":
        sys.stdout.write(raw)
        return

    # Find today's journal (try both layouts)
    journal_content = ""
    for jp in journal_paths_today():
        if jp.exists():
            try:
                journal_content = jp.read_text(encoding="utf-8", errors="replace")
                break
            except Exception:
                continue

    if not journal_content:
        # No journal yet — block with a clear message
        sys.stdout.write(json.dumps({
            "decision": "block",
            "reason": (
                "Wrap-up checklist enforcement: no personal journal entry exists for today. "
                "Run /wrap-up properly and ensure step 17 persists an authoritative entry to "
                "0-personal/notes/journals/YYYY-MM-DD.md with three sections: "
                "'Wrap-up Checklist Audit', 'Token Cost', 'Session Meta-Analysis'. "
                "Then retry plur_session_end."
            ),
        }))
        return

    missing = find_missing_sections(journal_content)
    if missing:
        sys.stdout.write(json.dumps({
            "decision": "block",
            "reason": (
                "Wrap-up checklist enforcement: today's personal journal is missing required "
                f"section(s): {', '.join(repr(s) for s in missing)}. "
                "Step 17 of /wrap-up requires an authoritative journal entry written from the "
                "main conversation. Step 18 requires an explicit checklist audit listing every "
                "spec step (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 12.5, 13, 14, 15, 16.5, 17, "
                "17.5, 18) with one of these statuses: 'run ✓', 'skipped-by-user', "
                "'skipped-by-mode-fast', 'not-applicable (REASON)', "
                "'inferred-and-reported (DESCRIPTION)', or 'applied-from-feedback (N CORRECTIONS)'. "
                "Append the missing section(s) to the journal and retry plur_session_end. "
                "See ENG-2026-0512-044 for context on why this is enforced."
            ),
        }))
        return

    # All required sections present — pass through
    sys.stdout.write(raw)


if __name__ == "__main__":
    main()
