"""A branch-scoped log is the same writer, not a foreign one (datacore#148).

The pre-push guard read the filename stem as the identity, so the first
salvaged run log — `nightshift-run-2026-09-06.jsonl` — was refused as another
actor's log and 27 commits could not be pushed. Third place to conflate the
file with the writer, after principal_of() and the seq high-water mark."""
from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB / "hooks"))
import log_ownership_guard as g  # noqa: E402


def test_base_writer_strips_only_a_dated_run_suffix():
    assert g.base_writer("nightshift-run-2026-09-06") == "nightshift"
    assert g.base_writer("miles-run-2026-09-06") == "miles"
    assert g.base_writer("nightshift") == "nightshift"
    assert g.base_writer("plur-run-team") == "plur-run-team"
    assert g.base_writer("mac-run-2026-9-6") == "mac-run-2026-9-6"
    assert g.base_writer("") == ""


def test_the_log_pattern_still_matches_a_run_scoped_file():
    m = g.ACTOR_LOG.match(".datacore/events/nightshift-run-2026-09-06.jsonl")
    assert m and m.group(1) == "nightshift-run-2026-09-06"
    assert g.base_writer(m.group(1)) == "nightshift"


def test_a_genuinely_foreign_log_is_still_foreign():
    for name in ("winston", "tris", "someone-else"):
        assert g.base_writer(f"{name}-run-2026-09-06") == name
    # the guard compares the BASE, so a foreign writer stays foreign
    assert g.base_writer("winston-run-2026-09-06") != "nightshift"
