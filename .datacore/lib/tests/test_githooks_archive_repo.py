"""A space's archive repo is not a space: the folder allowlist does not apply there.

WHY. The first real archive run (2026-10-18) copied 92 files from 3-fds into
3-fds-archive on the server, and the commit there was refused: the dispatcher
matched `[0-9]-*` and ran the space allowlist ("unexpected root directory:
nightshift/") and the conflict-marker rule on an archived log. An archive keeps
files verbatim under the paths they had in 4-archive/, so neither applies. The
credential rule still does, since archive repos push offsite.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_githooks_credential_store import FAKE, _commit, _install  # noqa: E402


def test_an_archive_repo_takes_any_folder_and_old_conflict_markers(tmp_path):
    root = _install(tmp_path)
    r = _commit(root / "3-fds-archive", root, "notes.md",
                "<<<<<<< ours\nold\n=======\nnew\n>>>>>>> theirs\n")
    assert r.returncode == 0, (r.stdout + r.stderr)[-400:]


def test_an_archive_repo_still_refuses_a_credential(tmp_path):
    root = _install(tmp_path)
    r = _commit(root / "3-fds-archive", root, "notes.env", FAKE)
    assert r.returncode != 0 and "credential pattern" in (r.stdout + r.stderr)


def test_a_space_still_refuses_conflict_markers(tmp_path):
    root = _install(tmp_path)
    r = _commit(root / "3-fds", root, "README.md", "<<<<<<< ours\na\n=======\nb\n>>>>>>> theirs\n")
    assert r.returncode != 0 and "conflict markers" in (r.stdout + r.stderr)
