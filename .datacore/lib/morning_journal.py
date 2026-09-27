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
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

DATA = Path.home() / "Data"
LIB = DATA / ".datacore" / "lib"
JOURNALS = DATA / "0-personal" / "notes" / "journals"
STATE = Path.home() / ".datacore" / "state" / "morning-journal"


def _candidates() -> list[str]:
    """Interpreters to try for the ledger sync, best first.

    launchd starts this script with /usr/bin/python3 (3.9 on macOS), and
    launchd's PATH has none of the user's interpreters on it, so the usual
    install locations are listed explicitly.
    """
    home = Path.home()
    names = [os.environ.get("DATACORE_PYTHON", ""), sys.executable]
    names += sorted((str(p) for p in (home / ".pyenv" / "versions").glob("3.1*/bin/python3")),
                    reverse=True)
    names += ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "python3"]
    out: list[str] = []
    for n in names:
        p = shutil.which(n) if n else None
        if p and p not in out:
            out.append(p)
    return out


def _can_import_ledger(py: str) -> bool:
    try:
        return subprocess.run(
            [py, "-c", f"import sys; sys.path.insert(0, {str(LIB)!r}); import ledger.log"],
            capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ledger_python() -> str | None:
    """The first interpreter that can import the ledger the sync runs on."""
    return next((py for py in _candidates() if _can_import_ledger(py)), None)


def notify(msg: str) -> None:
    """Best-effort desktop notification. The exit code carries the verdict.

    osascript can hang under launchd (seen 2026-09-27: 10 s timeout), and a
    notification that cannot be shown must not turn a clean "not delivered"
    into a crash.
    """
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification "{msg}" with title "Datacore morning"'],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"desktop notification not shown: {type(exc).__name__}", file=sys.stderr)


def main() -> int:
    today = date.today().isoformat()
    STATE.mkdir(parents=True, exist_ok=True)
    marker = STATE / f"opened-{today}"
    if marker.exists():
        print(f"{today}: already opened — nothing to do")
        return 0

    # The sync runs under an interpreter that can import the ledger, not
    # sys.executable: launchd's /usr/bin/python3 is 3.9 and the ledger needs
    # 3.10+. Until 2026-09-27 the sync crashed there every morning, its exit
    # code was ignored, and the job then blamed nightshift for a journal this
    # Mac had simply never pulled.
    py = ledger_python()
    if py is None:
        sync = subprocess.CompletedProcess([], 1, "", "no interpreter here can import the ledger")
    else:
        sync = subprocess.run(
            [py, str(LIB / "ledger_transport.py"), "sync", "--repo", "0-personal", "--quiet"],
            capture_output=True, text=True, timeout=300,
        )
    if (sync.stdout or "").strip():
        print(sync.stdout.strip())
    if (sync.stderr or "").strip():
        print(sync.stderr.strip(), file=sys.stderr)

    journal = JOURNALS / f"{today}.md"
    if sync.returncode != 0 and not journal.exists():
        msg = ("Morning journal sync FAILED on this Mac (rc=%d) — the briefing may be "
               "published but was not pulled" % sync.returncode)
        print(f"{today}: {msg}")
        notify(msg)
        return 1
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
        notify(msg)
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
