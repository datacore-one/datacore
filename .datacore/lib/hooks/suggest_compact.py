#!/usr/bin/env python3
"""Suggest Compact Hook (PreToolUse: Edit, Write)

Tracks tool call count per session. Suggests manual compaction at logical
intervals (default: every 50 tool calls) so context doesn't get auto-compacted
at bad times.
"""
import os
import sys
import tempfile

THRESHOLD = int(os.environ.get("COMPACT_THRESHOLD", "50"))
INTERVAL = 25  # suggest again every N calls after threshold


def main():
    raw = sys.stdin.read(1024 * 1024)

    session_id = os.environ.get("CLAUDE_SESSION_ID", "default")
    # Sanitize for filename
    safe_id = "".join(c for c in session_id if c.isalnum() or c in "-_")[:64] or "default"
    counter_file = os.path.join(tempfile.gettempdir(), f"claude-tool-count-{safe_id}")

    count = 1
    try:
        with open(counter_file) as f:
            prev = int(f.read().strip())
            if 0 < prev < 1_000_000:
                count = prev + 1
    except (FileNotFoundError, ValueError):
        pass

    try:
        with open(counter_file, "w") as f:
            f.write(str(count))
    except OSError:
        pass

    if count == THRESHOLD:
        sys.stderr.write(
            f"[Compact] {THRESHOLD} tool calls reached. "
            "Consider /compact if transitioning between phases.\n"
        )
    elif count > THRESHOLD and (count - THRESHOLD) % INTERVAL == 0:
        sys.stderr.write(
            f"[Compact] {count} tool calls. "
            "Good checkpoint for /compact if context is stale.\n"
        )

    sys.stdout.write(raw)
    sys.exit(0)


if __name__ == "__main__":
    main()
