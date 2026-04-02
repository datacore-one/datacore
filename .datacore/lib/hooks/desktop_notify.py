#!/usr/bin/env python3
"""Desktop Notification Hook (Stop)

Sends a macOS notification when Claude finishes responding.
Useful when running long tasks — get notified without watching the terminal.
"""
import json
import subprocess
import sys


def notify_macos(title, body):
    safe_body = body.replace("\\", "").replace('"', "\u201C")
    safe_title = title.replace("\\", "").replace('"', "\u201C")
    script = f'display notification "{safe_body}" with title "{safe_title}"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=5
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def extract_summary(text, max_len=100):
    if not text:
        return "Done"
    for line in text.split("\n"):
        line = line.strip()
        if line:
            return line[:max_len] + ("..." if len(line) > max_len else "")
    return "Done"


def main():
    raw = sys.stdin.read(1024 * 1024)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}

    summary = extract_summary(
        data.get("last_assistant_message", "")
        or data.get("assistant_message", "")
        or data.get("stop_reason", "")
    )
    notify_macos("Claude Code", summary)
    sys.stdout.write(raw)


if __name__ == "__main__":
    main()
