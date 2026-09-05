"""An item that also lives in inbox.org is compared nowhere, on either side (2026-09-06)."""
import pathlib, sys
LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
from ledger.log import EventLog  # noqa: E402
from ledger.shadow import compare  # noqa: E402


def test_generated_file_with_an_inbox_twin_is_clean(tmp_path):
    space = tmp_path / "0-personal"; (space / ".datacore" / "events").mkdir(parents=True); (space / "org").mkdir()
    log = EventLog(space, "mac")
    log.append("item.create", {"id": "cap-1", "title": "Captured tab", "state": "TODO", "tags": ["inbox"]})
    log.append("item.create", {"id": "t-2", "title": "Real task", "state": "NEXT"})
    (space / "org" / "inbox.org").write_text("* TODO Captured tab\n:PROPERTIES:\n:ID: cap-1\n:END:\n")
    from ledger.fold import fold
    from ledger.log import read_events
    from ledger.projector import project
    (space / "org" / "next_actions.org").write_text(project(fold(read_events(space)), space="0-personal").text)
    d = compare(space)
    assert d.clean, d
    assert d.org_count == d.projection_count == 1
