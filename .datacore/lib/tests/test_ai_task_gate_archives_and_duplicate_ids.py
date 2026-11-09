"""ai_task_gate judges what the executor queues, and refuses in words, never a traceback.

2026-10-28: Winston's inbox processing appended three digests to
0-personal/org/inbox_archive.org that were already archived under the same :ID:.
The space pre-commit hook ran this gate on the archive, org_workspace raised
DuplicateIdError, and the hook's answer was a Python traceback. The sync held
the file back and alerted every day. The executor never reads archive files
(nightshift_parser skips any name containing "archive"), so neither may the gate.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "ai_task_gate.py"

DUPES = """\
* Archived
** DONE Nightshift digest :nightshift:digest:
:PROPERTIES:
:ID: org-ns-digest-20261018
:END:
** CANCELLED Nightshift digest :nightshift:digest:
:PROPERTIES:
:ID: org-ns-digest-20261018
:END:
"""


def _run(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GATE), str(path)], capture_output=True, text=True,
                          cwd=path.parent, timeout=60)


def test_an_archive_file_is_not_gated(tmp_path):
    f = tmp_path / "inbox_archive.org"
    f.write_text(DUPES)
    r = _run(f)
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr


def test_duplicate_ids_in_a_queued_file_are_refused_in_words(tmp_path):
    f = tmp_path / "next_actions.org"
    f.write_text(DUPES)
    r = _run(f)
    assert r.returncode != 0
    assert "Traceback" not in r.stderr + r.stdout
    assert "org-ns-digest-20261018" in r.stderr + r.stdout
    assert "duplicate" in (r.stderr + r.stdout).lower()
