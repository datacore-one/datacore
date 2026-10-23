"""A prefix restore judges what it adds, not what the log already holds.

2026-10-23, 6-meridian on nightshift: the miles log lost its last two events to
a rescue branch. The restore refused with "the recovered chain does not verify:
True", because two events from 2026-09-25, already in the current log, were
signed by a key no machine still holds. The restore could never succeed, and
the message named nothing. A problem the current log already has is not the
restore's to judge; a problem the extension brings is, and must still refuse.
"""
import shutil
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from ledger.log import EventLog  # noqa: E402


def _flag_marked(monkeypatch):
    """A verifier that calls every event titled BAD a certain fault."""
    import ledger.verify as verify

    def fake(events, registry_path=None, strict=False):
        return [f"{e.actor}: seq {e.seq}: bad signature" for e in events
                if isinstance(e.payload, dict) and e.payload.get("title") == "BAD"]
    monkeypatch.setattr(verify, "chain_problems", fake)


def _parked(tmp_path, prefix_titles, parked_titles):
    sp = tmp_path / "s"
    log = EventLog(sp, "miles", sign=False)
    for i, t in enumerate(prefix_titles):
        log.append("item.create", {"id": f"a{i}", "title": t})
    path = sp / ".datacore/events/miles.jsonl"
    other = tmp_path / "o"
    (other / ".datacore/events").mkdir(parents=True)
    shutil.copy(path, other / ".datacore/events/miles.jsonl")
    ol = EventLog(other, "miles", sign=False)
    for i, t in enumerate(parked_titles):
        ol.append("item.create", {"id": f"p{i}", "title": t})
    return sp, path, (other / ".datacore/events/miles.jsonl").read_text()


def test_a_problem_already_in_the_log_does_not_block_the_restore(tmp_path, monkeypatch, capsys):
    import ledger_restore_prefix as rp
    _flag_marked(monkeypatch)
    sp, path, recovered = _parked(tmp_path, ["ok", "BAD", "ok"], ["ok", "ok"])
    monkeypatch.setattr(rp, "git", lambda repo, *a: recovered)

    assert rp.restore(sp, "miles", "rev", True) == 0
    assert path.read_text() == recovered
    out = capsys.readouterr().out
    assert "already in the current log" in out and "seq 1" in out


def test_a_problem_the_extension_brings_still_refuses_and_is_named(tmp_path, monkeypatch, capsys):
    import ledger_restore_prefix as rp
    _flag_marked(monkeypatch)
    sp, path, recovered = _parked(tmp_path, ["ok", "ok"], ["ok", "BAD"])
    before = path.read_text()
    monkeypatch.setattr(rp, "git", lambda repo, *a: recovered)

    assert rp.restore(sp, "miles", "rev", True) == 1
    assert path.read_text() == before
    out = capsys.readouterr().out
    assert "REFUSED" in out and "seq 3" in out and "True" not in out
