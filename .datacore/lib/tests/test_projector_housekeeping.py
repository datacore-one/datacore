"""A housekeeping closure is not finished work; the projection drops it at once (2026-09-05)."""
import pathlib, sys, time
LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
from ledger.fold import ItemState          # noqa: E402
from ledger.projector import projected_items  # noqa: E402


class _State:
    def __init__(self, items):
        self.items = {i.id: i for i in items}


def _closed(iid, reason, kind=None, status="dismissed"):
    it = ItemState(id=iid, title="Consider DIP for the guard pattern", owner=None, status=status,
                   payload={"level": 1, "state": "CANCELLED", "tags": []})
    it.closed_at = f"{int(time.time() * 1000)}.0000.mac"   # just now, inside retention
    it.closed_reason = reason
    if kind:
        it.closed_kind = kind
    return it


def test_a_twin_dismissed_as_housekeeping_is_not_rendered_for_the_retention_day():
    live = ItemState(id="org-new", title="Consider DIP for the guard pattern", owner=None, status="created",
                     payload={"level": 1, "state": "TODO", "tags": []})
    twin = _closed("org-old", "duplicate: superseded by org-new after the 2026-08-11 id regeneration", kind="housekeeping")
    ids = {i.id for i in projected_items(_State([live, twin]))}
    assert ids == {"org-new"}, ids


def test_work_actually_finished_today_is_still_shown():
    done = _closed("org-done", "finished", status="completed")
    dropped = _closed("org-dropped", "not doing this after review", kind="dropped")
    ids = {i.id for i in projected_items(_State([done, dropped]))}
    assert ids == {"org-done", "org-dropped"}, ids
