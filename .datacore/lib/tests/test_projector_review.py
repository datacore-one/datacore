"""An agent's completion is a hand-off, not a closure: it renders as REVIEW and never expires."""
import importlib.util, pathlib, sys
LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
from ledger.log import EventLog  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.projector import project, projected_items  # noqa: E402


def _space(tmp_path):
    sd = tmp_path / "5-plur"; (sd / ".datacore" / "events").mkdir(parents=True); return sd


def test_completed_item_renders_as_review_and_does_not_expire(tmp_path):
    sd = _space(tmp_path)
    log = EventLog(sd, "nightshift")
    log.append("item.create", {"id": "t1", "title": "Publish the trust page", "state": "TODO", "tags": ["plur"]})
    log.append("item.claim", {"id": "t1", "executor": "server:nightshift"})
    # closed_at is an HLC; fake one nine days old so a retention window would have dropped it
    old_ms = 1_700_000_000_000
    log.append("item.complete", {"id": "t1", "hlc": f"{old_ms}.0000.nightshift"})
    from ledger.log import read_events
    state = fold(list(read_events(sd)))
    items = projected_items(state, space="5-plur")
    assert [i.id for i in items] == ["t1"]
    text = project(state, space="5-plur").text
    assert "REVIEW Publish the trust page" in text
    assert "DONE Publish" not in text


def test_verified_and_dismissed_still_close(tmp_path):
    sd = _space(tmp_path)
    log = EventLog(sd, "mac")
    log.append("item.create", {"id": "a", "title": "A", "state": "TODO"})
    log.append("item.claim", {"id": "a", "executor": "mac"})
    log.append("item.complete", {"id": "a"})
    log.append("item.verify", {"id": "a"})
    log.append("item.create", {"id": "b", "title": "B", "state": "TODO"})
    log.append("item.dismiss", {"id": "b", "reason": "no longer needed", "kind": "dropped"})
    from ledger.log import read_events
    state = fold(list(read_events(sd)))
    text = project(state, space="5-plur").text
    assert "DONE A" in text and "CANCELLED B" in text
