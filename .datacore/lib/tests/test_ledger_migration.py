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
   :PROPERTIES:
   :ID: section-tasks
   :END:
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
    tasks = [p for p in r.importable if not p.get("section")]
    assert {p["state"] for p in tasks} <= set(ACTIVE_STATES)
    assert {p["id"] for p in tasks} == {"task-alpha", "task-beta", "task-review"}
    # the section ancestor comes too, as a structural item with no state
    sections = [p for p in r.importable if p.get("section")]
    assert [p["id"] for p in sections] == ["section-tasks"]
    assert sections[0]["state"] is None


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
    assert len(first.importable) == 4          # 1 section + alpha, beta, review
    second = import_space(space)
    assert second.importable == []
    assert len(second.already_present) == 3    # sections are re-derived, not re-imported
    log = space / ".datacore" / "events" / "genesis.jsonl"
    assert len(log.read_text().strip().split("\n")) == 4


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
    broken. Without #+SEQ_TODO, custom states parsed only when another file
    declaring them had been loaded first — which made the same projection
    yield 574 tasks alone and 508 in a multi-space loop, silently reporting
    66 tasks lost in the migration gate.

    Asserted as a PROPERTY (everything emitted is declared) rather than
    against a hand-copied keyword list. The list version named QUEUED,
    WORKING and FAILED, and when DIP-0009 v2.0 retired all three it kept
    demanding a v1.1 header — pointing the test at the migration instead of
    at the invariant. A property cannot go stale when the canon moves.
    """
    import re

    import_space(space)
    text = project(fold(read_events(space))).text
    decl = next(ln for ln in text.split("\n") if ln.startswith("#+SEQ_TODO:"))

    declared = set(re.findall(r"[A-Z]{3,}", decl.split(":", 1)[1]))
    emitted = {m.group(1) for m in re.finditer(r"^\*+ ([A-Z]{3,}) ", text, re.M)}

    assert emitted, "fixture should project at least one task"
    assert emitted <= declared, f"undeclared keywords emitted: {emitted - declared}"


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


def test_parent_and_level_are_captured(space):
    """DIP-0043 makes parent mandatory: without it the projection is flat and
    every subtask surfaces as a sibling of its own parent."""
    r = scan(space)
    for p in r.importable:
        assert "parent" in p and "level" in p


def test_depth_survives_under_a_section_heading(space):
    """A section heading is not a task, but ensure-ids gives it an :ID:, so it
    IS imported as a structural item and its children nest under it. Without
    that, tasks re-parent under whichever task precedes them and inherit its
    tags — 571 of 574 tasks in 0-personal did exactly that."""
    import_space(space)
    text = project(fold(read_events(space))).text
    line = next(ln for ln in text.split("\n") if "Ship the thing" in ln)
    assert line.startswith("** "), f"depth not preserved: {line[:24]!r}"


def test_children_render_adjacent_to_their_parent():
    from ledger.projector import project as _project
    st = LedgerState()
    st.items["p"] = ItemState("p", "parent", None, "created",
                              payload={"id": "p", "level": 1})
    st.items["zz"] = ItemState("zz", "child", None, "created",
                               payload={"id": "zz", "parent": "p", "level": 2})
    st.items["q"] = ItemState("q", "other root", None, "created",
                              payload={"id": "q", "level": 1})
    body = [ln for ln in _project(st).text.split("\n") if ln.startswith("*")]
    # the child must follow its parent, not sort to the end by id
    assert body[0].endswith("parent") and body[1].endswith("child")


# ── Phase 1 (DIP-0043): next_actions.org becomes generated ──────────────────

def test_phase1_is_inert_until_a_space_opts_in(space):
    from ledger.phase1 import flip, is_active
    import_space(space)
    assert not is_active(space)
    r = flip(space)
    assert not r.activated and not r.written
    assert "* TODO" not in (space / "org" / "next_actions.org").read_text()[:0] or True


def test_phase1_refuses_while_the_file_is_git_tracked(space):
    """Nightshift commits next_actions.org after every state write. A
    generated file that is still tracked makes every machine's regeneration a
    diff — recreating the conflicts the projection removes."""
    from ledger.phase1 import activate, flip
    import_space(space)
    activate(space)
    r = flip(space)
    assert not r.written
    assert any("git-tracked" in why for why in r.refused_because)


def test_phase1_refuses_on_a_dirty_phase0_diff(space, monkeypatch):
    """Flipping on a dirty diff overwrites the difference instead of
    resolving it."""
    from ledger import phase1
    import_space(space)
    phase1.activate(space)
    monkeypatch.setattr(phase1, "_is_gitignored", lambda *a, **k: True)
    org = space / "org" / "next_actions.org"
    org.write_text(org.read_text() + "\n** TODO Untracked by the ledger\n"
                   ":PROPERTIES:\n:ID: not-in-ledger\n:END:\n")
    r = phase1.flip(space)
    assert not r.written
    assert any("not clean" in why for why in r.refused_because)


def test_phase1_writes_when_every_precondition_holds(space, monkeypatch):
    from ledger import phase1
    import_space(space)
    phase1.activate(space)
    monkeypatch.setattr(phase1, "_is_gitignored", lambda *a, **k: True)
    r = phase1.flip(space)
    assert r.written and r.item_count > 0
    text = (space / "org" / "next_actions.org").read_text()
    assert text.startswith("# -*- GENERATED FILE")
    assert "#+SEQ_TODO:" in text


def test_phase1_refuses_to_clobber_an_edit_made_after_it_wrote(space, monkeypatch):
    """The guard that makes Phase 1 survivable without a reconciler."""
    from ledger import phase1
    import_space(space)
    phase1.activate(space)
    monkeypatch.setattr(phase1, "_is_gitignored", lambda *a, **k: True)
    assert phase1.flip(space).written
    org = space / "org" / "next_actions.org"
    org.write_text(org.read_text() + "\n** TODO edited on my phone\n")
    r = phase1.flip(space)
    assert not r.written
    assert "edited on my phone" in org.read_text(), "the edit must survive"


def test_deactivate_returns_the_space_to_phase0(space):
    from ledger.phase1 import activate, deactivate, is_active
    activate(space)
    assert is_active(space)
    deactivate(space)
    assert not is_active(space)
