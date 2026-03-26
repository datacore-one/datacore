#!/usr/bin/env python3
"""SessionStart hook — lightweight bootstrap.

Cleans up stale session state from crashed sessions.
Outputs journal + candidate count as additionalContext.
Does NOT inject engrams (no task context yet — that happens on first UserPromptSubmit).

Input: JSON on stdin with {source: "startup"|"resume"|"clear"|"compact", ...}
Output: JSON on stdout with {additionalContext} or empty (exit 0)
"""
import json, sys, os, glob
from datetime import date
from pathlib import Path

# Get absolute paths using DATACORE_ROOT
DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
sys.path.insert(0, str(DATACORE_ROOT / ".datacore" / "lib"))
from session_state import cleanup_stale_sessions, _debug

def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        input_data = {}

    source = input_data.get("source", "startup")
    _debug(f"session_bootstrap: source={source}")

    # Clean stale state from crashed sessions (older than 24h)
    cleanup_stale_sessions()

    # Gather lightweight context (no engram injection)
    lines = []

    # Today's journal
    today = date.today().isoformat()
    journal_paths = [
        DATACORE_ROOT / "0-personal" / "notes" / "journals" / f"{today}.md",
        DATACORE_ROOT / "0-personal" / "journal" / f"{today}.md",
    ]
    for jp in journal_paths:
        if jp.exists():
            lines.append(f"Journal today: {jp}")
            break

    # Count candidate engrams
    engram_files = list(DATACORE_ROOT.glob(".datacore/learning/engrams.yaml"))
    candidate_count = 0
    try:
        import yaml
        for ef in engram_files:
            with open(ef) as f:
                data = yaml.safe_load(f) or {}
                engs = data.get("engrams", []) if isinstance(data, dict) else data
                candidate_count += sum(1 for e in engs if e.get("status") == "candidate")
    except Exception:
        pass

    if candidate_count > 0:
        lines.append(f"{candidate_count} candidate engram(s) awaiting review.")

    lines.append("Datacore session initialized. Engrams will inject on first message.")

    if lines:
        context = "[Datacore Session Bootstrap]\n\n" + "\n".join(lines)
        json.dump({"additionalContext": context}, sys.stdout)

    sys.exit(0)

if __name__ == "__main__":
    main()
