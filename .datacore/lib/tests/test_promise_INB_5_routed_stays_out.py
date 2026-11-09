"""Promise INB-5 (catalogue GT-03, audit B-F6):

    Once an item has been routed it leaves the inbox for good, and never comes
    back as a fresh copy.

Seeded failure: a session writes inbox.org from a stale snapshot, resurrecting
an item that the morning job already routed to next_actions.org — under a NEW
:ID:, so id-based dedup cannot see it. Headings are the join key. The eval
goes red when the morning job's automatic pass
(`inbox_dedup.py --space-all --exact-only --apply`) leaves the resurrected
copy in the inbox, removes a near-match it cannot be sure of, writes without a
backup, or fails to report the near-match.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
TOOL = LIB / "inbox_dedup.py"

ROUTED = "Book the dentist appointment for October"
ROUTED_L1 = "Review plur-ai pull request queue"
NEAR = "Email Bob about the Q4 contract"
NEAR_ROUTED = "email bob about the Q4 contract."
FRESH = "Brand new capture nobody routed"

INBOX = f"""#+TITLE: Inbox

* Inbox
** TODO {ROUTED}
:PROPERTIES:
:ID: resurrected-new-id-1
:END:
Stale-snapshot body line.
** TODO {NEAR}
:PROPERTIES:
:ID: near-1
:END:
** TODO {FRESH}
:PROPERTIES:
:ID: fresh-1
:END:
* TODO [#A] {ROUTED_L1} :github:
:PROPERTIES:
:ID: resurrected-new-id-2
:END:
"""

NEXT_ACTIONS = f"""#+TITLE: Next Actions

* Personal
** TODO {ROUTED} :health:
:PROPERTIES:
:ID: routed-original-1
:END:
** TODO {NEAR_ROUTED}
:PROPERTIES:
:ID: routed-original-2
:END:
* Work
** NEXT {ROUTED_L1}
:PROPERTIES:
:ID: routed-original-3
:END:
"""

UNTOUCHED = f"""#+TITLE: Inbox

* Inbox
** TODO {FRESH}
:PROPERTIES:
:ID: other-fresh
:END:
"""


def _space(root: Path, name: str, inbox: str, next_actions: str | None) -> Path:
    org = root / name / "org"
    org.mkdir(parents=True)
    (org / "inbox.org").write_text(inbox, encoding="utf-8")
    if next_actions is not None:
        (org / "next_actions.org").write_text(next_actions, encoding="utf-8")
    return org


def _run(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), "--root", str(root), "--space-all", "--exact-only", *extra],
        capture_output=True, text=True, timeout=60,
    )


def _headings(text: str) -> list[str]:
    return [l for l in text.splitlines() if l.startswith("*")]


def test_resurrected_copy_is_removed_and_near_match_kept(tmp_path):
    root = tmp_path / "Data"
    org = _space(root, "0-personal", INBOX, NEXT_ACTIONS)
    other = _space(root, "5-plur", UNTOUCHED, NEXT_ACTIONS)

    proc = _run(root, "--apply")
    assert proc.returncode == 0, proc.stderr

    inbox = (org / "inbox.org").read_text(encoding="utf-8")
    heads = "\n".join(_headings(inbox))
    assert ROUTED not in heads, "a routed item resurrected with a new ID must leave the inbox"
    assert "resurrected-new-id-1" not in inbox and "Stale-snapshot body line." not in inbox
    assert ROUTED_L1 not in heads, "a top-level resurrected capture must leave the inbox too"
    assert NEAR in heads, "a near-match is ambiguous and must stay"
    assert FRESH in heads, "an unrouted capture must stay"
    assert "* Inbox" in heads

    backup = org / "inbox.org.bak"
    assert backup.exists() and backup.read_text(encoding="utf-8") == INBOX

    assert NEAR in proc.stdout, "the ambiguous near-match must be reported"
    # routed copies in next_actions.org are untouched
    assert (org / "next_actions.org").read_text(encoding="utf-8") == NEXT_ACTIONS
    # a space with nothing to remove is not rewritten and gets no backup
    assert (other / "inbox.org").read_text(encoding="utf-8") == UNTOUCHED
    assert not (other / "inbox.org.bak").exists()


def test_dry_run_writes_nothing(tmp_path):
    root = tmp_path / "Data"
    org = _space(root, "0-personal", INBOX, NEXT_ACTIONS)
    proc = _run(root)
    assert proc.returncode == 0, proc.stderr
    assert (org / "inbox.org").read_text(encoding="utf-8") == INBOX
    assert not (org / "inbox.org.bak").exists()
    assert ROUTED in proc.stdout


def test_heading_in_an_archive_file_is_not_a_routed_copy(tmp_path):
    """Archives hold what left the inbox as finished; a heading found only there
    is not proof the live item was routed, so the capture stays."""
    root = tmp_path / "Data"
    org = _space(root, "0-personal", INBOX, None)
    (org / "inbox_archive.org").write_text(NEXT_ACTIONS, encoding="utf-8")
    proc = _run(root, "--apply")
    assert proc.returncode == 0, proc.stderr
    assert (org / "inbox.org").read_text(encoding="utf-8") == INBOX
