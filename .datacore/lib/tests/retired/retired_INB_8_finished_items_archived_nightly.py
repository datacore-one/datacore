"""Promise INB-8:

    Finished items move out of the inbox into an archive every night, so the
    inbox shows only open captures.

Kind: deterministic plus a production contract.
  - deterministic: the nightly hygiene step (`gtd_hygiene.py --data-dir`,
    which nightshift run.py phase 9 calls and which calls inbox_cleanup.py per
    space) moves every DONE/CANCELLED entry — top-level, in the "* Inbox"
    section, or nested under a closed task — to org/inbox-archive-<date>.org,
    keeps open and DEFERRED entries, and loses nothing;
  - production (read-only, this machine's synced Data): no inbox holds a
    finished entry that was CLOSED more than 36 hours ago, i.e. the nightly
    archive actually ran.

Seeded failure: inbox_cleanup skips the "* Inbox" section, or the nightly run
stops calling the hygiene step (finished entries pile up: 22 in 0-personal on
2026-09-26, closed 2026-09-25 05:03).
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]

INBOX = """#+TITLE: Inbox

* Inbox
** TODO Open capture one
:PROPERTIES:
:ID: open-1
:END:
** DONE Finished in the section
CLOSED: [2026-09-20 Sun 09:00]
:PROPERTIES:
:ID: done-1
:END:
** DEFERRED Wake me in October
:PROPERTIES:
:ID: def-1
:END:
* CANCELLED Top-level cancelled capture
CLOSED: [2026-09-20 Sun 09:00]
:PROPERTIES:
:ID: canc-1
:END:
** TODO Open capture buried under a closed task
:PROPERTIES:
:ID: open-2
:END:
* DONE Top-level done capture
CLOSED: [2026-09-20 Sun 09:00]
:PROPERTIES:
:ID: done-2
:END:
"""


def _headings(text: str) -> dict[str, str]:
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^\*+\s+([A-Z]+)\s+(.*)$", line)
        if m:
            cur = (m.group(1), m.group(2))
            continue
        m = re.match(r"^\s*:ID:\s*(\S+)", line)
        if m and cur:
            out[m.group(1)] = cur[0]
            cur = None
    return out


def test_nightly_hygiene_archives_every_finished_entry_and_keeps_the_open_ones(tmp_path):
    data = tmp_path / "Data"
    org = data / "0-personal" / "org"
    org.mkdir(parents=True)
    (org / "inbox.org").write_text(INBOX, encoding="utf-8")
    (org / "next_actions.org").write_text("#+TITLE: Next Actions\n\n* Work\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(LIB / "gtd_hygiene.py"), "--data-dir", str(data), "--json"],
                       capture_output=True, text=True, timeout=60, cwd=str(LIB))
    assert r.returncode == 0, (r.stderr or r.stdout)[-1500:]
    left = _headings((org / "inbox.org").read_text(encoding="utf-8"))
    assert set(left) == {"open-1", "open-2", "def-1"}, f"inbox after the nightly step: {left}"
    archives = list(org.glob("inbox-archive-*.org"))
    assert archives, "no archive file was written"
    archived = {}
    for a in archives:
        archived.update(_headings(a.read_text(encoding="utf-8")))
    assert {"done-1", "canc-1", "done-2"} <= set(archived), f"finished entries lost: {archived}"


def _stale_finished(path: Path, now: datetime) -> list[str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    for i, line in enumerate(lines):
        m = re.match(r"^\*+\s+(DONE|CANCELLED)\s+(.*)$", line)
        if not m:
            continue
        for nxt in lines[i + 1:i + 3]:
            c = re.search(r"CLOSED:\s*\[(\d{4}-\d{2}-\d{2})[^\]]*?(\d{2}:\d{2})?\]", nxt)
            if c:
                when = datetime.fromisoformat(c.group(1) + " " + (c.group(2) or "00:00"))
                if now - when > timedelta(hours=36):
                    out.append(f"{m.group(2)[:50]} (closed {c.group(1)})")
                break
    return out


@pytest.mark.production
def test_no_inbox_holds_entries_finished_more_than_a_night_ago():
    now = datetime.now()
    stale = {}
    for inbox in sorted(ROOT.glob("[0-9]-*/org/inbox.org")):
        s = _stale_finished(inbox, now)
        if s:
            stale[inbox.parent.parent.name] = s
    assert not stale, "finished entries still in inboxes: " + "; ".join(
        f"{k}: {len(v)} e.g. {v[0]}" for k, v in stale.items())
