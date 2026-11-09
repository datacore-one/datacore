"""LED-1: Every task change and every agent action in a space is recorded in
that space's history, in the order it happened.

Kind: deterministic. Two disposable spaces under a tmp root; the real hourly
sweep (`ledger_ingest_org.main`) records what org says, and the real nightshift
hooks (`modules/nightshift/lib/ledger_hooks.py`) record what an agent did.

Sequence driven, in real time order, for task `a1` in space 0-alpha:
  capture (org NEXT)          -> item.create   (sweep)
  reschedule (org SCHEDULED)  -> item.update   (sweep)
  agent claims it             -> item.claim    (nightshift hook)
  agent starts / finishes     -> item.clock.start, item.clock.stop, item.complete
Then the promise, as assertions:
  * each step appears in 0-alpha's history, exactly once, in that order
    (merged `read_events` order == real order, per-log seq strictly rising);
  * nothing about a1 lands in 1-beta and nothing about b1 lands in 0-alpha;
  * the history verifies (`verify_chain`) and folds to a completed item.

Seeded failure: the sweep stops reconciling field changes
(`ledger_ingest_org.sync_state` returns without emitting) -- the reschedule
is then never recorded and the eval goes red.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

LIB = Path(__file__).resolve().parents[1]
NIGHTSHIFT_LIB = LIB.parent / "modules" / "nightshift" / "lib"

import ledger_ingest_org as ingest  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.log import read_events  # noqa: E402
from ledger.verify import verify_chain  # noqa: E402

ORG_A = "* NEXT Write the report\n:PROPERTIES:\n:ID: a1\n:END:\nBody.\n"
ORG_A_RESCHEDULED = ("* NEXT Write the report\nSCHEDULED: <2026-10-01 Thu>\n"
                     ":PROPERTIES:\n:ID: a1\n:END:\nBody.\n")
ORG_B = "* TODO Other space task\n:PROPERTIES:\n:ID: b1\n:END:\n"


def _sweep(root: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["ledger_ingest_org.py", "--root", str(root)])
    assert ingest.main() == 0


def _hooks():
    if str(NIGHTSHIFT_LIB) not in sys.path:
        sys.path.insert(0, str(NIGHTSHIFT_LIB))
    import importlib.util
    spec = importlib.util.spec_from_file_location("ns_ledger_hooks", NIGHTSHIFT_LIB / "ledger_hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def root(tmp_path, monkeypatch):
    root = tmp_path / "Data"
    for name, text in (("0-alpha", ORG_A), ("1-beta", ORG_B)):
        (root / name / "org").mkdir(parents=True)
        (root / name / "org" / "next_actions.org").write_text(text)
    monkeypatch.setattr(ingest, "_this_actor", lambda: "sweeper")
    monkeypatch.setattr(ingest, "_notify_daemon", lambda root: None)
    monkeypatch.setenv("DATACORE_ACTOR", "nightshift")
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    return root


def _ids(space: Path) -> set[str]:
    return {(e.payload or {}).get("id") for e in read_events(space)}


def test_every_task_change_and_agent_action_is_recorded_in_order(root, monkeypatch):
    alpha, beta = root / "0-alpha", root / "1-beta"

    _sweep(root, monkeypatch)                               # capture
    (alpha / "org" / "next_actions.org").write_text(ORG_A_RESCHEDULED)
    _sweep(root, monkeypatch)                               # reschedule

    hooks = _hooks()
    task = SimpleNamespace(id="a1", file_path=str(alpha / "org" / "next_actions.org"))
    assert hooks.claimed(task, "server:nightshift"), "the agent's claim was not recorded"
    hooks.lifecycle("started", task, "exec-1", agent="gtd-content-writer")
    hooks.lifecycle("completed", task, "exec-1", score=0.9, output="0-inbox/report.md")

    events = [e for e in read_events(alpha) if (e.payload or {}).get("id") == "a1"]
    types = [e.type for e in events]
    expected = ["item.create", "item.update", "item.claim",
                "item.clock.start", "item.clock.stop", "item.complete"]
    assert types == expected, f"history for a1 is {types}, expected {expected}"

    update = events[1].payload
    assert "2026-10-01" in str(update), f"the reschedule is not what was recorded: {update}"

    hlcs = [e.hlc for e in events]
    assert hlcs == sorted(hlcs) and len(set(hlcs)) == len(hlcs), "recorded out of order"
    by_log: dict[str, list[int]] = {}
    for e in events:
        by_log.setdefault(e.log, []).append(e.seq)
    for log, seqs in by_log.items():
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), f"{log}: seq not rising {seqs}"

    assert "b1" not in _ids(alpha), "another space's task landed in 0-alpha"
    assert "a1" not in _ids(beta), "0-alpha's task landed in 1-beta"
    assert "b1" in _ids(beta), "1-beta's own task was not recorded"

    for path in sorted((alpha / ".datacore" / "events").glob("*.jsonl")):
        assert verify_chain(path) == [], f"{path.name} does not verify"
    assert fold(read_events(alpha)).items["a1"].status == "completed"


def test_a_state_change_in_org_is_recorded_after_the_create(root, monkeypatch):
    alpha = root / "0-alpha"
    _sweep(root, monkeypatch)
    org = alpha / "org" / "next_actions.org"
    org.write_text(ORG_A.replace("* NEXT", "* WAITING"))
    _sweep(root, monkeypatch)
    org.write_text(ORG_A.replace("* NEXT", "* DONE"))
    _sweep(root, monkeypatch)
    types = [e.type for e in read_events(alpha) if (e.payload or {}).get("id") == "a1"]
    assert types[0] == "item.create", types
    assert "item.update" in types, f"NEXT -> WAITING was not recorded: {types}"
    assert types[-1] == "item.dismiss", f"DONE was not recorded last: {types}"
    assert types.index("item.update") < types.index("item.dismiss")
