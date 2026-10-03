"""item.reopen and item.archive (ledger upgrade Phase 4), replayed against the real fold.

Each test mirrors a theorem in specs/datacore-lean/LedgerSpec/Item.lean, which was
written first:

  reopen_revives                 a dismissed or archived item comes back open,
                                 unowned and ungranted, content and conflicts kept
  archive_is_revivable           archive hides without closing; reopen restores it
  reopen_of_open_work_is_noop    completed / verified / live work is never reopened
  reopen_is_the_only_way_back    nothing else revives a dismissed or archived item
  dismissed_frozen               a dismissed item ignores every event but reopen
"""
from ledger.events import EVENT_TYPES, Event
from ledger.fold import fold
from ledger.projector import project


def _ev(seq, actor, type_, payload):
    return Event(seq=seq, hlc=f"{seq + 1}.0.{actor}", actor=actor, type=type_, payload=payload,
                 prev="", hash=f"h{seq}", sig="")


def _run(*steps):
    return fold([_ev(n, actor, t, p) for n, (actor, t, p) in enumerate(steps)])


CREATE = ("mac", "item.create", {"id": "t1", "title": "Write the report", "state": "TODO"})


def test_both_event_types_are_declared():
    assert {"item.reopen", "item.archive"} <= EVENT_TYPES


def test_reopen_revives_a_dismissed_item_with_its_content():
    st = _run(CREATE,
              ("mac", "item.claim", {"id": "t1"}),
              ("mac", "item.dismiss", {"id": "t1", "kind": "done", "reason": "closed as DONE"}),
              ("mac", "item.reopen", {"id": "t1", "reason": "reopened in next_actions.org"}))
    it = st.items["t1"]
    assert it.status == "created"
    assert it.owner is None and it.granted_by is None
    assert it.closed_at is None and it.closed_kind is None and it.closed_reason is None
    assert it.payload["title"] == "Write the report"
    assert it.history[-1].endswith("item.reopen: applied")


def test_archive_hides_without_closing_and_reopen_restores():
    st = _run(CREATE, ("mac", "item.archive", {"id": "t1", "reason": "archived to next_actions.org_archive"}))
    it = st.items["t1"]
    assert it.status == "archived" and it.payload["title"] == "Write the report"
    assert "t1" not in project(st, space=None).text, "an archived item must not be rendered"

    st = _run(CREATE, ("mac", "item.archive", {"id": "t1"}), ("mac", "item.reopen", {"id": "t1"}))
    assert st.items["t1"].status == "created"
    assert "Write the report" in project(st, space=None).text


def test_reopen_of_open_or_finished_work_changes_nothing():
    for tail in ([], [("mac", "item.claim", {"id": "t1"})],
                 [("mac", "item.claim", {"id": "t1"}), ("mac", "item.complete", {"id": "t1"})],
                 [("mac", "item.claim", {"id": "t1"}), ("mac", "item.complete", {"id": "t1"}),
                  ("pi", "item.verify", {"id": "t1"})]):
        before = _run(CREATE, *tail).items["t1"]
        after = _run(CREATE, *tail, ("mac", "item.reopen", {"id": "t1"})).items["t1"]
        assert (after.status, after.owner, after.closed_at) == (before.status, before.owner, before.closed_at)
        assert "no-op" in after.history[-1]


def test_an_archived_item_cannot_be_worked_but_can_be_edited_or_closed():
    for t, p in (("item.claim", {"id": "t1"}), ("owner.set", {"id": "t1", "owner": "pi"})):
        it = _run(CREATE, ("mac", "item.archive", {"id": "t1"}), ("pi", t, p)).items["t1"]
        assert it.status == "archived" and it.owner is None, t
    it = _run(CREATE, ("mac", "item.archive", {"id": "t1"}),
              ("mac", "item.update", {"id": "t1", "title": "Renamed while archived"})).items["t1"]
    assert it.status == "archived" and it.title == "Renamed while archived"
    it = _run(CREATE, ("mac", "item.archive", {"id": "t1"}),
              ("mac", "item.dismiss", {"id": "t1", "kind": "dropped"})).items["t1"]
    assert it.status == "dismissed"


def test_nothing_but_reopen_revives_a_dismissed_item():
    closed = (CREATE, ("mac", "item.dismiss", {"id": "t1", "kind": "done"}))
    for t, p in (("item.archive", {"id": "t1"}), ("owner.set", {"id": "t1", "owner": "pi"}),
                 ("item.claim", {"id": "t1"}), ("item.update", {"id": "t1", "title": "x"}),
                 ("item.create", {"id": "t1", "title": "again"})):
        it = _run(*closed, ("pi", t, p)).items["t1"]
        assert it.status == "dismissed" and it.title == "Write the report", t


def test_a_space_without_the_new_events_folds_exactly_as_before():
    st = _run(CREATE, ("mac", "item.dismiss", {"id": "t1", "kind": "done"}))
    assert st.items["t1"].status == "dismissed"
    assert st.items["t1"].history[-1].endswith("item.dismiss: applied")
