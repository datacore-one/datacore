"""The whole-ledger truths, asserted in one sweep.

On 2026-09-18 exactly ONE event in 67,610 had a payload that no longer matched
its recorded hash. That event made `latest_jobs` raise, which made
`claim_gate.absent` report EVERY principal absent, which stamped
`assignee_absent` on every delegated item. It was found by hand, in a
transcript, by someone who happened to be looking. Nothing asserted it.

These tests inject each breakage this sweep exists to notice, because a sweep
that reports SOUND on a broken ledger is worse than no sweep at all.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import actor_identity  # noqa: E402
import ledger_invariants as inv  # noqa: E402
from ledger.log import EventLog  # noqa: E402


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    registry = tmp_path / ".datacore/registry/principals.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("principals:\n  worker: {kind: agent, writes_as: [worker]}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", registry)
    actor_identity._PRINCIPALS_CACHE.clear()
    space = tmp_path / "1-work"
    log = EventLog(space, "worker", sign=False)
    for n in range(3):
        log.append("item.create", {"id": f"i{n}", "title": f"task {n}"})
    return tmp_path, space


def _names(findings):
    return {f.invariant for f in findings if not f.unknown}


def test_a_healthy_ledger_is_sound(fleet):
    root, _ = fleet
    assert _names(inv.sweep(root)) == set()


def test_an_edited_payload_is_caught(fleet):
    """The 2026-09-18 event, reproduced: content changed, hash left behind."""
    root, space = fleet
    log = space / ".datacore/events/worker.jsonl"
    log.write_text(log.read_text().replace('"title": "task 1"', '"title": "tampered"')
                   .replace('"title":"task 1"', '"title":"tampered"'))

    assert "hashes" in _names(inv.sweep(root))


def test_a_conflict_marker_is_caught(fleet):
    """What a raw `git add` of a conflicted writer log leaves behind."""
    root, space = fleet
    log = space / ".datacore/events/worker.jsonl"
    lines = log.read_text().splitlines()
    lines.insert(2, "<<<<<<< HEAD")
    log.write_text("\n".join(lines) + "\n")

    assert "parseable" in _names(inv.sweep(root))


def test_a_rewound_chain_is_caught(fleet):
    root, space = fleet
    log = space / ".datacore/events/worker.jsonl"
    lines = log.read_text().splitlines()
    log.write_text("\n".join(lines[:1] + lines[2:]) + "\n")   # drop seq 1

    assert "chains" in _names(inv.sweep(root))


def test_an_undeclared_writer_is_caught(fleet):
    """A log that signs but belongs to no principal -- found live on
    2-datacore/ceo.jsonl, which has a verify key and no principals entry."""
    root, space = fleet
    EventLog(space, "stranger", sign=False).append("item.create", {"id": "s1", "title": "t"})

    findings = inv.sweep(root)
    assert "declared" in _names(findings)
    assert any("stranger" in f.detail for f in findings)


def test_a_root_with_no_spaces_refuses_to_report_sound(tmp_path, capsys):
    assert inv.main(["--root", str(tmp_path)]) == 2
    assert "refusing" in capsys.readouterr().out


def test_quick_skips_the_expensive_checks_but_still_finds_structure(fleet):
    root, space = fleet
    log = space / ".datacore/events/worker.jsonl"
    log.write_text(log.read_text().replace('"title": "task 1"', '"title": "tampered"')
                   .replace('"title":"task 1"', '"title":"tampered"'))

    assert "hashes" not in _names(inv.sweep(root, quick=True))
    EventLog(space, "stranger", sign=False).append("item.create", {"id": "s2", "title": "t"})
    assert "declared" in _names(inv.sweep(root, quick=True))


def test_events_merely_awaiting_the_next_converge_do_not_page(fleet, monkeypatch):
    """`gaps()` is not ok while events are pending, which is the normal state
    between a local append and the next hourly converge. Alerting on that would
    page after every single write -- and an alert that cries wolf stops being
    read, which is the failure this whole exercise exists to prevent."""
    root, _ = fleet

    class _R:
        ok = False
        reason = "3 event(s) not yet published in 1 log(s), inside the grace window"
        context = {"rows": [{"log": "worker", "pending": 3, "gap": 0}]}

    monkeypatch.setattr("ledger_transport.gaps", lambda space: _R())
    assert "published" not in _names(inv.sweep(root))


def test_events_stranded_past_the_grace_window_do_page(fleet, monkeypatch):
    root, _ = fleet

    class _R:
        ok = False
        reason = "1 log(s) unpublished"
        context = {"rows": [{"log": "worker", "pending": 9, "gap": 9}]}

    monkeypatch.setattr("ledger_transport.gaps", lambda space: _R())
    assert "published" in _names(inv.sweep(root))


# --- The accepted-findings baseline ---------------------------------------
# A new audit that is red on its first day teaches everyone to ignore it. The
# two findings live on 2026-09-19 are both owner decisions -- one needs a
# principal declared, the other would mean rewriting published ledger history.
# Neither should page anyone nightly; neither should be forgotten either.

def _baseline_file(tmp_path, entries):
    import yaml
    path = tmp_path / "baseline.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "accepted": entries}))
    return path


def test_an_accepted_finding_does_not_fail_the_sweep(fleet, tmp_path, capsys):
    root, space = fleet
    EventLog(space, "stranger", sign=False).append("item.create", {"id": "s1", "title": "t"})
    baseline = _baseline_file(tmp_path, [
        {"invariant": "declared", "space": "1-work",
         "detail_startswith": "stranger: belongs to no declared principal"}])

    assert inv.main(["--root", str(root), "--baseline", str(baseline), "--quick"]) == 0
    assert "accepted by the owner" in capsys.readouterr().out


def test_a_different_finding_in_the_same_space_still_fails(fleet, tmp_path):
    """An allowlist, not a mute button: a SECOND bad writer is new."""
    root, space = fleet
    EventLog(space, "stranger", sign=False).append("item.create", {"id": "s1", "title": "t"})
    EventLog(space, "interloper", sign=False).append("item.create", {"id": "s2", "title": "t"})
    baseline = _baseline_file(tmp_path, [
        {"invariant": "declared", "space": "1-work",
         "detail_startswith": "stranger: belongs to no declared principal"}])

    assert inv.main(["--root", str(root), "--baseline", str(baseline), "--quick"]) == 1


def test_a_missing_baseline_accepts_nothing(fleet, tmp_path):
    root, space = fleet
    EventLog(space, "stranger", sign=False).append("item.create", {"id": "s1", "title": "t"})

    assert inv.main(["--root", str(root), "--baseline", str(tmp_path / "absent.yaml"),
                     "--quick"]) == 1


def test_the_shipped_baseline_is_readable_and_reasoned(tmp_path):
    """Every entry must say when it was accepted and why, or it is a mute
    button that nobody can argue with later."""
    import yaml
    shipped = Path(inv.LIB).parent / "config" / "ledger-invariants-baseline.yaml"
    if not shipped.exists():
        pytest.skip("no baseline shipped in this checkout")
    data = yaml.safe_load(shipped.read_text())
    for entry in data.get("accepted") or []:
        assert entry.get("accepted_on"), entry
        assert entry.get("why"), entry
        assert entry.get("invariant") and entry.get("space"), entry
