"""briefing_materialize: the caller briefing.actions never had (DIP-0038).

Covers the two guarantees that make re-running safe -- exactly-once by content
hash, and side-effecting proposals refused rather than silently created.
"""
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import briefing_materialize as bm


def _artifact(entries):
    return {"delegate": entries}


def test_proposals_extracts_task_text():
    a = _artifact([{"task": "Merge PR #16", "why": "unblocks queue", "agent_hint": "nightshift"}])
    assert bm.proposals(a) == [{"text": "Merge PR #16"}]


def test_why_and_agent_hint_are_not_part_of_identity():
    """Rephrased reasoning must not change an item's id, or exactly-once breaks."""
    a = _artifact([{"task": "Merge PR #16", "why": "one reason"}])
    b = _artifact([{"task": "Merge PR #16", "why": "a completely different reason"}])
    assert bm.proposals(a) == bm.proposals(b)


def test_effects_forwarded_when_declared():
    a = _artifact([{"task": "Email the client", "effects": ["email.send"]}])
    assert bm.proposals(a) == [{"text": "Email the client", "effects": ["email.send"]}]


def test_effects_never_inferred_from_prose():
    """Guessing an effect would gate the wrong work in both directions."""
    a = _artifact([{"task": "Send an email to the investor and deploy to prod"}])
    assert "effects" not in bm.proposals(a)[0]


def test_blank_and_malformed_entries_dropped():
    a = _artifact([{"task": "   "}, {"why": "no task key"}, "not a dict", {"task": "real"}])
    assert bm.proposals(a) == [{"text": "real"}]


def test_missing_delegate_key_is_empty_not_an_error():
    assert bm.proposals({}) == []


def test_exactly_once_across_two_runs(tmp_path):
    """A briefing regenerated after a failed run must not duplicate items."""
    art = tmp_path / "b.json"
    art.write_text(json.dumps(_artifact([{"task": "Merge PR #16"}])))
    space = tmp_path / "space"
    space.mkdir()
    assert bm.main(["--artifact", str(art), "--space", str(space), "--actor", "t"]) == 0
    # second run over the same briefing creates nothing new
    assert bm.main(["--artifact", str(art), "--space", str(space), "--actor", "t"]) == 0
    events = (space / ".datacore" / "events" / "t.jsonl").read_text().strip().split("\n")
    assert len(events) == 1, f"expected exactly one item.create, got {len(events)}"


def test_side_effecting_proposal_is_blocked_not_created(tmp_path):
    """The co-sign gate: no grant means refused, never silently created."""
    art = tmp_path / "b.json"
    art.write_text(json.dumps(_artifact([
        {"task": "Send an unapproved email", "effects": ["email.send"]},
    ])))
    space = tmp_path / "space"
    space.mkdir()
    rc = bm.main(["--artifact", str(art), "--space", str(space), "--actor", "t"])
    assert rc == 1, "a blocked proposal must be reported via exit code"
    log = space / ".datacore" / "events" / "t.jsonl"
    assert not log.exists() or log.read_text().strip() == "", "nothing may be written"
