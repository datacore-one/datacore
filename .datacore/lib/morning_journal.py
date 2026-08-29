#!/usr/bin/env python3
"""Morning journal delivery on the Mac — pull 0-personal, open the briefing.

Restores the "the journal opens for me in the morning" ritual without any
autonomous execution on the Mac (three-machine split, 2026-07-27): the box
and nightshift produce the briefing; this job only fetches and opens it.

Run by launchd (io.datacore.morning-journal) at 07:30 and 08:30 local —
two shots because nightshift publishes the journal after its batch, and
the Oura-gated box chain can push the morning past 08:00. A daily marker
prevents a second open once it has been shown.

Safe by construction: sync goes through the single transport (commit-first,
never stash, never rebase), and this script itself never writes to the repo.
"""
import subprocess
import sys
from datetime import date
from pathlib import Path

DATA = Path.home() / "Data"
JOURNALS = DATA / "0-personal" / "notes" / "journals"
STATE = Path.home() / ".datacore" / "state" / "morning-journal"


def main() -> int:
    today = date.today().isoformat()
    STATE.mkdir(parents=True, exist_ok=True)
    marker = STATE / f"opened-{today}"
    if marker.exists():
        print(f"{today}: already opened — nothing to do")
        return 0

    sync = subprocess.run(
        [sys.executable, str(DATA / ".datacore" / "lib" / "ledger_transport.py"),
         "sync", "--repo", "0-personal", "--quiet"],
        capture_output=True, text=True, timeout=300,
    )
    print(sync.stdout.strip())
    if sync.stderr.strip():
        print(sync.stderr.strip(), file=sys.stderr)

    journal = JOURNALS / f"{today}.md"
    # "## Daily Briefing" check retired 2026-07-29: miles_delivery paste was
    # retired; briefing now ships as audio + Telegram + app card — nothing
    # writes that heading into the journal anymore. Keep only the file-exists
    # check; if the journal is absent the overnight batch failed entirely.
    # See datacore#54.
    missing = "journal not published" if not journal.exists() else None
    if missing:
        # Single daily shot (08:30) — a miss must be LOUD, not a log line.
        # Silent non-delivery is exactly what the 2026-07-29 post-mortem
        # was about.
        msg = f"Morning briefing NOT delivered ({missing}) — check nightshift on the server"
        print(f"{today}: {msg}")
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{msg}" with title "Datacore morning"'],
            capture_output=True, timeout=10,
        )
        return 1

    subprocess.run(["open", str(journal)], timeout=30)
    marker.write_text("")
    # Keep only the last 14 markers.
    for old in sorted(STATE.glob("opened-*"))[:-14]:
        old.unlink()
    print(f"{today}: briefing opened")
    return 0


if __name__ == "__main__":
    sys.exit(main())
