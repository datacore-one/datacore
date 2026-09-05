#!/usr/bin/env python3
"""Native Messaging host for Datacore Tab Capture.

Receives tab data from the browser extension via stdin (Chrome Native Messaging
protocol), deduplicates against existing inbox.org entries, appends new tabs as
org-mode TODO entries, and returns the result via stdout.
"""

import fcntl
import json
import os
import re
import struct
import sys
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

DEFAULT_CONFIG = {
    "inbox_path": "~/Data/0-personal/org/inbox.org",
    "filtered_prefixes": [
        "brave://", "chrome://", "about:", "chrome-extension://", "devtools://"
    ]
}

INBOX_HEADER = """\
#+TITLE: Inbox
#+CATEGORY: Inbox
#+FILETAGS: :inbox:

* Inbox
"""


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    return DEFAULT_CONFIG


def read_message():
    """Read a Native Messaging message from stdin."""
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return None
    length = struct.unpack("=I", raw_length)[0]
    data = sys.stdin.buffer.read(length)
    return json.loads(data.decode("utf-8"))


def send_message(msg):
    """Send a Native Messaging message to stdout."""
    encoded = json.dumps(msg).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def extract_sources(content):
    """Extract all :SOURCE: property values from org content."""
    sources = set()
    for match in re.finditer(r"^:SOURCE:\s+(.+)$", content, re.MULTILINE):
        sources.add(match.group(1).strip())
    return sources


def is_filtered(url, prefixes):
    """Check if a URL should be filtered out."""
    return any(url.startswith(p) for p in prefixes)


def format_entry(tab, today_str):
    """Format a tab as an org-mode TODO entry."""
    title = tab.get("title", "").strip() or tab["url"]
    if len(title) > 120:
        title = title[:117] + "..."
    url = tab["url"]
    lines = [
        # Top level: inbox.org is a flat list of entries. A "**" heading appended
        # at the end became a child of whatever entry was last -- on 2026-09-05
        # 85 captured tabs sat folded under a DONE task and were invisible to
        # every outline view and to the GTD tools.
        f"* TODO [[{url}][{title}]]",
        ":PROPERTIES:",
        f":SOURCE: {url}",
        f":CAPTURED: [{today_str}]",
        ":END:",
    ]
    return "\n".join(lines)


def capture_tabs(tabs, config):
    """Capture tabs to inbox.org, returning result stats."""
    inbox_path = os.path.expanduser(config["inbox_path"])
    filtered_prefixes = config.get("filtered_prefixes", DEFAULT_CONFIG["filtered_prefixes"])
    today_str = date.today().strftime("%Y-%m-%d %a")

    # Filter out internal browser pages
    tabs = [t for t in tabs if not is_filtered(t["url"], filtered_prefixes)]

    if not tabs:
        return {"success": True, "count": 0, "duplicates_skipped": 0}

    # Ensure inbox.org exists
    inbox_dir = os.path.dirname(inbox_path)
    if inbox_dir and not os.path.exists(inbox_dir):
        os.makedirs(inbox_dir, exist_ok=True)

    if not os.path.exists(inbox_path):
        with open(inbox_path, "w") as f:
            f.write(INBOX_HEADER)

    # Read and deduplicate with file locking
    with open(inbox_path, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            content = f.read()
            existing_sources = extract_sources(content)

            new_tabs = [t for t in tabs if t["url"] not in existing_sources]
            duplicates_skipped = len(tabs) - len(new_tabs)

            if new_tabs:
                entries = "\n".join(format_entry(t, today_str) for t in new_tabs)
                # Ensure we start on a new line
                if content and not content.endswith("\n"):
                    f.write("\n")
                f.write(entries + "\n")
                f.flush()

            return {
                "success": True,
                "count": len(new_tabs),
                "duplicates_skipped": duplicates_skipped,
            }
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def main():
    config = load_config()
    msg = read_message()

    if msg is None:
        send_message({"success": False, "error": "No message received"})
        return

    action = msg.get("action", "")
    if action == "capture":
        tabs = msg.get("tabs", [])
        result = capture_tabs(tabs, config)
        send_message(result)
    else:
        send_message({"success": False, "error": f"Unknown action: {action}"})


if __name__ == "__main__":
    main()
