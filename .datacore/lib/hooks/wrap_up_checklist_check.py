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
from datetime import date, timedelta
from pathlib import Path


REQUIRED_SECTIONS = (
    "Wrap-up Checklist Audit",
    "Token Cost",
    "Session Meta-Analysis",
)


def journal_paths_recent() -> list[Path]:
    """Today's journal AND yesterday's.

    Sessions that roll past midnight legitimately have their wrap-up content
    on yesterday's journal (the session-start date). The hook should accept
    either, so we don't force a thin handoff entry on today's journal just
    to satisfy a date check.
    """
    today = date.today()
    yesterday = today - timedelta(days=1)
    paths = []
    for d in (today, yesterday):
        iso = d.isoformat()
        paths.append(Path.home() / "Data" / "0-personal" / "notes" / "journals" / f"{iso}.md")
        paths.append(Path.home() / "Data" / "0-personal" / "journal" / f"{iso}.md")
    return paths


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

    # Find a journal with the required sections — accept today OR yesterday.
    # Sessions that roll past midnight have content on the session-start date,
    # not today's date. Check both, accept the first that satisfies.
    journal_content = ""
    missing_per_journal: list[tuple[str, list[str]]] = []
    for jp in journal_paths_recent():
        if not jp.exists():
            continue
        try:
            content = jp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        missing = find_missing_sections(content)
        if not missing:
            # All sections present in this one — accept
            journal_content = content
            break
        missing_per_journal.append((str(jp), missing))

    if journal_content:
        # All required sections present somewhere recent — pass through
        sys.stdout.write(raw)
        return

    if not missing_per_journal:
        # No journal at all in the last 2 days — block
        sys.stdout.write(json.dumps({
            "decision": "block",
            "reason": (
                "Wrap-up checklist enforcement: no personal journal entry exists for today "
                "or yesterday. Run /wrap-up properly and ensure step 17 persists an "
                "authoritative entry to 0-personal/notes/journals/YYYY-MM-DD.md with three "
                "sections: 'Wrap-up Checklist Audit', 'Token Cost', 'Session Meta-Analysis'. "
                "Then retry plur_session_end."
            ),
        }))
        return

    # Some journal exists but is missing sections — report which ones
    # (use the journal with the fewest missing sections — likely the active one)
    missing_per_journal.sort(key=lambda x: len(x[1]))
    best_path, missing = missing_per_journal[0]
    sys.stdout.write(json.dumps({
        "decision": "block",
        "reason": (
            f"Wrap-up checklist enforcement: journal at {best_path} is missing required "
            f"section(s): {', '.join(repr(s) for s in missing)}. "
            "Step 17 of /wrap-up requires an authoritative journal entry written from the "
            "main conversation. Step 18 requires an explicit checklist audit listing every "
            "spec step (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 12.5, 13, 14, 15, 16.5, 17, "
            "17.5, 18) with one of these statuses: 'run ✓', 'skipped-by-user', "
            "'skipped-by-mode-fast', 'not-applicable (REASON)', "
            "'inferred-and-reported (DESCRIPTION)', or 'applied-from-feedback (N CORRECTIONS)'. "
            "Sessions that roll past midnight can keep the audit on the session-start date — "
            "this hook accepts today's OR yesterday's journal. Append the missing section(s) "
            "and retry plur_session_end. See ENG-2026-0512-044 for context on why this is enforced."
        ),
    }))


if __name__ == "__main__":
    main()
