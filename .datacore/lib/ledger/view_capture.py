"""One door in (owner decision 8, 2026-10-04): an unclear edit to a view is HELD in the inbox.

Only `org/inbox.org` is written by hand. `next_actions.org` and the other task
files are views the ledger regenerates. When a view has been edited anyway, the
cycle diffs it against what the ledger last wrote (`last-rendered.json`):

  * a PLAIN change to a task (close, reopen, a note, a date, a retitle, a
    priority, tags) and a clear new task are applied to the ledger by code in
    that same cycle (`projection_state.sync_generated`);
  * an UNCLEAR one -- a removed heading, a copied subtree (a duplicate :ID:),
    a clash with an agent's edit, a hand reorder, an edited CREATED stamp -- is
    held here as exactly ONE inbox entry, marked [NEEDS_REVIEW], naming the
    task (:VIEW_EDIT_OF:), what changed (:VIEW_CHANGE:) and the file
    (:VIEW_FILE:), with the human's text quoted so nothing is lost.

Then the view is regenerated, so the same edit is never captured twice. No edit
is reverted unseen or lost, and no edit stops a space.

The quoted text is written as org fixed-width lines (`  : ...`), so a quoted
heading or :ID: line is never parsed as a heading or a property of the inbox.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path

INBOX = Path("org/inbox.org")


@dataclass
class Held:
    of: str             # the task id the edit was made to
    change: str         # what changed, in plain words
    title: str          # the task's title, for the heading
    text: str           # the human's version (heading or subtree), quoted verbatim
    view: str = "next_actions.org"


def _stamp() -> str:
    return time.strftime("[%Y-%m-%d %a %H:%M]", time.localtime())


def render(h: Held) -> str:
    title = " ".join((h.title or h.of).split())[:120]
    quoted = "\n".join(f"  : {line}" if line else "  :" for line in h.text.rstrip("\n").split("\n"))
    return (f"* TODO [NEEDS_REVIEW] Edit to {h.view} held for review: {title}\n"
            f"  :PROPERTIES:\n"
            f"  :ID: {uuid.uuid4()}\n"
            f"  :VIEW_EDIT_OF: {h.of}\n"
            f"  :VIEW_CHANGE: {h.change}\n"
            f"  :VIEW_FILE: {h.view}\n"
            f"  :CREATED: {_stamp()}\n"
            f"  :END:\n"
            f"  {h.view} is a view of the ledger, so this edit was not read back into it.\n"
            f"  Decide what it should mean, then remove this entry. The edit as it was typed:\n"
            f"{quoted}\n")


def hold(space: Path, held: list[Held]) -> int:
    """Append one inbox entry per held edit, inside the org transaction."""
    if not held:
        return 0
    from org_transaction import serialized, watch_file, write_org_text

    @serialized
    def _append():
        path = Path(space) / INBOX
        watch_file(path)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current and not current.endswith("\n"):
            current += "\n"
        write_org_text(path, current + "".join(render(h) for h in held))
    _append()
    return len(held)


def archived_in(space: Path, item_id: str) -> bool:
    """Positive evidence that `item_id` was archived: it sits in an org archive file."""
    org = Path(space) / "org"
    needle = f":ID: {item_id}"
    for f in list(org.glob("*_archive")) + list(org.glob("*_archive.org")) + list(org.glob("*.org_archive")):
        try:
            if any(line.strip() == needle for line in f.read_text(encoding="utf-8", errors="replace").splitlines()):
                return True
        except OSError:
            continue
    return False
