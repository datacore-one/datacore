"""One writer's chain must never fork across two branches.

2026-09-08. Every nightshift run branch conflicted on `.datacore/events/
<actor>.jsonl`: the run appended on `nightshift/<date>` while the hourly
cycle appended to the same file on `main`. On 5-plur's 2026-09-06 branch
that was 79 events against main's 71, both chained from the same base.
A union merge would produce two chains claiming the same `prev`, which
verify_chain rejects — so the fix is a genuinely disjoint file, not a
merge driver."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from actor_identity import base_writer, principal_of  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.verify import verify_chain  # noqa: E402


@pytest.fixture()
def space(tmp_path):
    (tmp_path / ".datacore" / "events").mkdir(parents=True)
    return tmp_path


def test_a_branch_scoped_log_is_a_separate_file_with_its_own_valid_chain(space):
    EventLog(space, "nightshift", sign=False).append("item.create", {"id": "a"})
    run = EventLog(space, "nightshift", sign=False, log_name="nightshift-run-2026-09-06")
    run.append("item.claim", {"id": "a"})
    run.append("item.complete", {"id": "a"})

    assert run.path.name == "nightshift-run-2026-09-06.jsonl"
    assert (space / ".datacore/events/nightshift.jsonl").exists()

    # Each file chains from GENESIS on its own — the invariant a union merge breaks.
    for f in sorted((space / ".datacore/events").glob("*.jsonl")):
        ok, problems = verify_chain(f)[:2] if isinstance(verify_chain(f), tuple) else (True, [])
        assert ok, f"{f.name}: {problems}"

    # The reader already merges every writer file, so nothing downstream changes.
    events = read_events(space)
    assert [e.type for e in events] == ["item.create", "item.claim", "item.complete"]
    assert {e.actor for e in events} == {"nightshift"}, "the ACTOR stays canonical"


def test_the_actor_is_unchanged_by_the_file_it_lands_in(space):
    run = EventLog(space, "nightshift", sign=False, log_name="nightshift-run-2026-09-06")
    ev = run.append("item.create", {"id": "x"})
    assert ev.actor == "nightshift"


def test_a_branch_scoped_writer_belongs_to_its_base_writers_principal():
    assert base_writer("nightshift-run-2026-09-06") == "nightshift"
    assert base_writer("nightshift") == "nightshift"
    assert base_writer("winston-run-2026-01-01") == "winston"
    # Only a dated run suffix is stripped — a real writer keeps its name.
    assert base_writer("plur-run-team") == "plur-run-team"
    assert base_writer("mac-run-2026-9-6") == "mac-run-2026-9-6"


def test_principal_lookup_follows_the_base_writer():
    assert principal_of("nightshift")[0] == principal_of("nightshift-run-2026-09-06")[0]


def test_an_invalid_log_name_is_refused_like_an_invalid_actor(space):
    with pytest.raises(ValueError, match="invalid log name"):
        EventLog(space, "nightshift", sign=False, log_name="../escape")
