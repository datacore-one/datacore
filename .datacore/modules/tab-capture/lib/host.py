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
        "brave://", "chrome://", "about:", "chrome-extension://", "devtools://",
        "chrome-untrusted://", "view-source:"
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
        f"** TODO [[{url}][{title}]]",
        ":PROPERTIES:",
        f":SOURCE: {url}",
        f":CAPTURED: [{today_str}]",
        ":END:",
    ]
    return "\n".join(lines)


def insert_under_inbox(content, entries):
    """Place new entries at the end of the "* Inbox" section.

    Entries are "**" headings, so where they land decides whose children
    they are. Appending at the end of the file put every batch under
    whatever top-level entry was last: on 2026-09-05, 85 captured tabs sat
    folded beneath a DONE task, invisible to every outline view and to the
    GTD tools. The section is the first top-level heading titled Inbox
    (case-insensitive); entries go before the next top-level heading after
    it. A file without such a section gets one appended.
    """
    lines = content.split("\n")
    start = next((i for i, l in enumerate(lines)
                  if re.match(r"^\* +(inbox)\s*$", l, re.IGNORECASE)), None)
    if start is None:
        base = content.rstrip("\n")
        return (base + "\n\n" if base else "") + "* Inbox\n" + entries + "\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("* ")), len(lines))
    while end > start + 1 and lines[end - 1].strip() == "":
        end -= 1
    return "\n".join(lines[:end] + entries.split("\n") + lines[end:])


def capture_tabs(tabs, config):
    """Capture tabs to inbox.org, returning result stats."""
    inbox_path = os.path.expanduser(config["inbox_path"])
    # An installed config.json written before a prefix was added must not let
    # that internal page through: the defaults always apply.
    filtered_prefixes = list(dict.fromkeys(
        config.get("filtered_prefixes", []) + DEFAULT_CONFIG["filtered_prefixes"]))
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

    # Optimistic read-modify-write: read, build the new inbox, then swap it in
    # only if the file still holds exactly what was read. Anything else wrote
    # meanwhile (the task adapter, a sync pull, another host) -> read again.
    # Writing into the handle we read from was the MEM-32 loss: the adapter
    # replaces inbox.org by atomic rename, so a write through an old handle
    # lands in the orphaned file and the tab capture vanishes.
    for _ in range(5):
        with open(inbox_path, "r") as f:
            content = f.read()
        existing_sources = extract_sources(content)

        # One entry per page: already in the inbox, or open in two tabs
        # of this same save (CAP-4).
        new_tabs, seen = [], set(existing_sources)
        for t in tabs:
            if t["url"] not in seen:
                seen.add(t["url"])
                new_tabs.append(t)
        duplicates_skipped = len(tabs) - len(new_tabs)
        result = {"success": True, "count": len(new_tabs),
                  "duplicates_skipped": duplicates_skipped}
        if not new_tabs:
            return result
        entries = "\n".join(format_entry(t, today_str) for t in new_tabs)
        if swap_if_unchanged(inbox_path, content, insert_under_inbox(content, entries)):
            return result
    return {"success": False, "error": "inbox.org kept changing while saving; nothing written, try again"}


def swap_if_unchanged(path, expected, new):
    """Replace `path` with `new` only if it still holds `expected`.

    Under Datacore's org lock when the core lib is present (the same lock the
    task adapter holds for its whole read-modify-write, so neither can slip a
    write between the other's check and replace); otherwise under a flock on
    the inbox. The write is an atomic rename either way.
    """
    lib = os.path.join(SCRIPT_DIR, "..", "..", "..", "lib")
    try:
        if lib not in sys.path:
            sys.path.insert(0, lib)
        from org_transaction import read_text, serialized, watch_file, write_org_text
    except ImportError:
        return _swap_flock(path, expected, new)
    from pathlib import Path

    @serialized(timeout=10)
    def swap():
        target = Path(path).resolve()
        watch_file(target)
        if read_text(target) != expected:
            return False
        write_org_text(target, new)
        return True
    return swap()


def _swap_flock(path, expected, new):
    with open(path, "r") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            if f.read() != expected:
                return False
            tmp = f"{path}.tab-capture.{os.getpid()}"
            with open(tmp, "w") as out:
                out.write(new)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, path)
            return True
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
        # A capture that cannot be saved says why (CAP-3). Dying here leaves
        # the extension with only "Native host has exited".
        try:
            result = capture_tabs(tabs, config)
        except Exception as exc:  # noqa: BLE001 — every failure must reach the popup
            result = {"success": False, "count": 0,
                      "error": f"could not save to {config.get('inbox_path', 'the inbox')}: "
                               f"{type(exc).__name__}: {exc}"}
        send_message(result)
    else:
        send_message({"success": False, "error": f"Unknown action: {action}"})


if __name__ == "__main__":
    main()
