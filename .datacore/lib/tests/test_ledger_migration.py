"""Genesis import + org projection (DIP-0043) — the migration.

Covers the properties that make it safe to run before anyone trusts it:
idempotence, a valid-time ladder that never invents a date, determinism, and
the refuse-to-overwrite guard.
"""
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ledger.fold import ItemState, LedgerState, fold  # noqa: E402
from ledger.genesis import (  # noqa: E402
    ACTIVE_STATES,
    GENESIS_FALLBACK,
    OVERLAY_STATES,
    import_space,
    scan,
)
from ledger.log import read_events  # noqa: E402
from ledger.projector import (  # noqa: E402
    ProjectionConflict,
    project,
    render_item,
    write,
)

ORG = """* Tasks
** TODO [#A] Ship the thing                                    :work:urgent:
   SCHEDULED: <2026-08-11 Tue>
   :PROPERTIES:
   :ID: task-alpha
   :CREATED: [2026-07-01 Wed]
   :OWNER: gregor
   :END:
   Body line one.
** WAITING Waiting on someone
   :PROPERTIES:
   :ID: task-beta
   :CREATED: [2026-06-15 Mon]
   :END:
** DONE Already finished
   :PROPERTIES:
   :ID: task-done
   :END:
** REVIEW Overlay state from nightshift
   :PROPERTIES:
   :ID: task-review
   :END:
** TODO Task with no id at all
"""


@pytest.fixture
def space(tmp_path):
    d = tmp_path / "9-test"
    (d / "org").mkdir(parents=True)
    (d / "org" / "next_actions.org").write_text(ORG)
    return d


def test_scan_takes_every_live_state_including_overlays(space):
    """A task in REVIEW is live work wearing an execution badge, not history.
    Excluding overlays once left 87 real tasks unmigratable."""
    r = scan(space)
    assert {p["state"] for p in r.importable} <= set(ACTIVE_STATES)
    assert {p["id"] for p in r.importable} == {"task-alpha", "task-beta", "task-review"}


def test_overlay_state_is_preserved_and_flagged(space):
    r = scan(space)
    review = next(p for p in r.importable if p["id"] == "task-review")
    assert review["state"] == "REVIEW", "the overlay state must survive verbatim"
    assert review["overlay"] is True
    alpha = next(p for p in r.importable if p["id"] == "task-alpha")
    assert alpha["overlay"] is False


def test_finished_states_are_reported_not_silently_dropped(space):
    """DONE/CANCELLED stay behind as history — but visibly, never silently."""
    r = scan(space)
    assert r.out_of_scope.get("DONE") == 1
    assert "REVIEW" not in r.out_of_scope, "overlays migrate now, not skipped"


def test_task_without_an_id_is_reported(space):
    r = scan(space)
    assert len(r.missing_id) == 1


def test_scan_writes_nothing(space):
    scan(space)
    assert not (space / ".datacore" / "events").exists()


def test_import_is_idempotent(space):
    first = import_space(space)
    assert len(first.importable) == 3          # alpha, beta, review
    second = import_space(space)
    assert second.importable == []
    assert len(second.already_present) == 3
    log = space / ".datacore" / "events" / "genesis.jsonl"
    assert len(log.read_text().strip().split("\n")) == 3


def test_valid_time_comes_from_created_property(space):
    r = scan(space)
    alpha = next(p for p in r.importable if p["id"] == "task-alpha")
    assert alpha["genesis"] == {"date": "2026-07-01", "rung": "created_property"}


def test_fallback_date_is_never_rendered_as_a_real_created(tmp_path):
    """A defaulted date must not masquerade as a known one."""
    item = ItemState(
        id="x", title="T", owner=None, status="created",
        payload={"id": "x", "title": "T", "state": "TODO",
                 "genesis": {"date": GENESIS_FALLBACK, "rung": "genesis_fallback"}},
    )
    assert "CREATED" not in "\n".join(render_item(item))


def test_projection_is_deterministic(space):
    import_space(space)
    state = fold(read_events(space))
    assert project(state).text == project(state).text
    assert project(fold(read_events(space))).text == project(state).text


def test_projection_preserves_task_content(space):
    import_space(space)
    text = project(fold(read_events(space))).text
    assert "** TODO [#A] Ship the thing" in text
    assert ":urgent:work:" in text   # SORTED — see test_tags_are_sorted
    assert "SCHEDULED: <2026-08-11 Tue>" in text
    assert ":ID: task-alpha" in text
    assert "Body line one." in text
    assert "** WAITING Waiting on someone" in text


def test_tags_are_sorted_not_set_ordered(space):
    """org-workspace returns tags as a SET, so an unsorted join renders a
    different byte string per process under hash randomisation. This test
    exists because the suite caught exactly that: one run produced
    ':work:urgent:', another ':urgent:work:', from identical input."""
    import_space(space)
    text = project(fold(read_events(space))).text
    line = next(ln for ln in text.split("\n") if "Ship the thing" in ln)
    rendered = line.split(":", 1)[1].rstrip(":").split(":")
    assert rendered == sorted(rendered), f"tags not sorted: {rendered}"


def test_projection_excludes_finished_items():
    state = LedgerState()
    state.items["a"] = ItemState("a", "live", None, "created", payload={"id": "a"})
    state.items["b"] = ItemState("b", "gone", None, "completed", payload={"id": "b"})
    text = project(state).text
    assert "live" in text and "gone" not in text


def test_projection_order_is_stable_regardless_of_insertion(tmp_path):
    """Item order must not depend on the order events happened to arrive."""
    def build(ids):
        st = LedgerState()
        for i in ids:
            st.items[i] = ItemState(i, f"t{i}", None, "created", payload={"id": i})
        return project(st).text
    assert build(["c", "a", "b"]) == build(["a", "b", "c"])


def test_write_refuses_to_clobber_an_edited_file(space, tmp_path):
    """The guard that makes Phase 1 safe without a reconciler."""
    import_space(space)
    proj = project(fold(read_events(space)))
    target = tmp_path / "next_actions.org"
    written = write(proj, target)
    target.write_text(target.read_text() + "\n** TODO edited on my phone\n")
    with pytest.raises(ProjectionConflict) as exc:
        write(proj, target, last_written_sha=written)
    assert "refusing to overwrite" in str(exc.value)
    assert "edited on my phone" in target.read_text(), "the edit must survive"


def test_write_proceeds_when_the_file_is_untouched(space, tmp_path):
    import_space(space)
    proj = project(fold(read_events(space)))
    target = tmp_path / "next_actions.org"
    sha = write(proj, target)
    assert write(proj, target, last_written_sha=sha) == sha


def test_generated_header_is_present(space):
    import_space(space)
    assert project(fold(read_events(space))).text.startswith("# -*- GENERATED FILE")


def test_projection_declares_its_own_todo_keywords(space):
    """A generated file whose parse depends on what was read before it is
    broken. Without #+SEQ_TODO, custom states (REVIEW, QUEUED, ...) parsed
    only when another file declaring them had been loaded first — which made
    the same projection yield 574 tasks alone and 508 in a multi-space loop,
    silently reporting 66 tasks lost in the migration gate."""
    import_space(space)
    text = project(fold(read_events(space))).text
    decl = next(ln for ln in text.split("\n") if ln.startswith("#+SEQ_TODO:"))
    for state in ("REVIEW", "QUEUED", "WORKING", "FAILED", "DEFERRED"):
        assert state in decl, f"{state} must be declared"


def test_overlay_task_survives_a_cold_reparse(space, tmp_path):
    """Parse the projection in isolation — no other org file read first."""
    import_space(space)
    out = tmp_path / "cold.org"
    out.write_text(project(fold(read_events(space))).text)
    import sys as _s, pathlib as _p
    _s.path.insert(0, str(_p.Path(__file__).resolve().parents[1]))
    from org_workspace import OrgWorkspace
    ws = OrgWorkspace(); ws.load(str(out))
    states = {n.todo for n in ws.all_nodes() if n.todo}
    assert "REVIEW" in states, f"overlay state lost on cold reparse: {states}"
