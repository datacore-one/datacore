#!/usr/bin/env python3
"""PostToolUse on plur_session_start: creates sentinel file so the guard allows subsequent tools."""
import json
import sys
import pathlib
import tempfile


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    session_id = data.get("session_id", "")
    if not session_id:
        return

    sentinel = pathlib.Path(f"{tempfile.gettempdir()}/plur-session-{session_id}")
    sentinel.touch()


if __name__ == "__main__":
    main()
