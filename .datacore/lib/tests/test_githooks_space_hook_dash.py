"""The space hook reads a staged file as it is, under every /bin/sh (2026-10-24).

WHY. The space hook printed each staged file with `echo "$blob"`. Where /bin/sh
is dash (the agent hosts), echo turns a two-character `\\n` inside a file into a
real newline, so a test that holds the string "<<<<<<< ours\\n=======..." read
as a file with conflict markers at the start of a line and the commit was
refused. Hermes could not commit the Datacore upgrade for this reason. The rule
is unchanged: real markers at the start of a line are still refused.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_githooks_credential_store import _commit, _install  # noqa: E402

DASH = shutil.which("dash")
pytestmark = pytest.mark.skipif(DASH is None, reason="needs dash, the /bin/sh of the agent hosts")


def _under_dash(root: Path) -> Path:
    """The installed hooks, run by dash as on a host whose /bin/sh is dash."""
    for hook in (root / ".datacore" / "hooks").iterdir():
        if hook.is_file() and hook.read_text(errors="replace").startswith("#!/bin/sh"):
            hook.write_text(hook.read_text().replace("#!/bin/sh", f"#!{DASH}", 1))
    return root


def test_an_escaped_newline_inside_a_string_is_not_a_conflict_marker(tmp_path):
    root = _under_dash(_install(tmp_path))
    r = _commit(root / "3-fds", root, "README.md",
                'FIXTURE = "<<<<<<< ours\\nold\\n=======\\nnew\\n>>>>>>> theirs\\n"\n')
    assert r.returncode == 0, (r.stdout + r.stderr)[-400:]


def test_real_conflict_markers_are_still_refused_under_dash(tmp_path):
    root = _under_dash(_install(tmp_path))
    r = _commit(root / "3-fds", root, "README.md", "<<<<<<< ours\na\n=======\nb\n>>>>>>> theirs\n")
    assert r.returncode != 0 and "conflict markers" in (r.stdout + r.stderr)
