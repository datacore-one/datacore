"""A heading that arrives already closed must not stop a phase-1 space forever.

`genesis.scan` admits only ACTIVE states, so a DONE or CANCELLED heading the
ledger has never seen is skipped as out of scope. In a phase-1 space the
three-way merge in `sync_generated` then meets a heading that is in the file,
not in the base and not in the ledger, and refuses: "new heading is not
admitted to the ledger; ingest first". Ingest is the thing that skipped it, so
the remedy the message names cannot be followed, and the space's ingest and
projection stop every hour on every host.

Live case: 5-plur on winston from 2026-09-27 05:25Z, one heading
(org-20260924-gatemem-rerun-submission-prep) that reached the generated file
already DONE. Same class as a heading without a TODO keyword
(test_plain_heading_is_admitted.py): the admission rule and the projection's
rule disagreed about which headings exist.

The fix admits it and closes it in the same pass: the ledger records that the
task existed and was done, rather than pretending the heading is not there.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import ledger_ingest_org as ingest  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.genesis import import_space, scan  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.projector import project  # noqa: E402
from ledger.projection_state import STATE, base_document  # noqa: E402

CLOSED = ("* DONE Rerun the benchmark and prepare the submission\n"
          "  :PROPERTIES:\n"
          "  :ID:       closed-before-admission\n"
          "  :CREATED:  [2026-09-24 Thu]\n"
          "  :END:\n")


def _phase1_space(tmp_path: Path, marker: str = "1") -> Path:
    space = tmp_path / "9-fixture"
    (space / "org").mkdir(parents=True)
    (space / ".datacore" / "events").mkdir(parents=True)
    (space / ".datacore" / "ledger-phase").write_text(marker + "\n")
    (space / ".datacore" / "ledger-edit-protocol").write_text("1\n")
    EventLog(space, "writer").append("item.create", {
        "id": "open-one", "title": "An open task", "state": "TODO", "space": space.name, "level": 1,
        "tags": [], "org": {"body": "", "properties": {}, "priority": None}})
    text = project(fold(read_events(space)), space=space.name).text
    (space / "org" / "next_actions.org").write_text(text)
    (space / STATE).parent.mkdir(parents=True, exist_ok=True)
    (space / STATE).write_text(base_document(text))
    return space


def test_a_closed_heading_the_ledger_never_saw_does_not_stop_the_sweep(tmp_path):
    space = _phase1_space(tmp_path)
    org = space / "org" / "next_actions.org"
    org.write_text(org.read_text() + CLOSED)

    import_space(space, actor="writer")
    ingest.sync_state(space, actor="writer")          # raised "ingest first" before the fix

    item = fold(read_events(space)).items["closed-before-admission"]
    assert item.status == "dismissed" and item.closed_kind == "done"


def test_running_the_sweep_again_admits_nothing_more(tmp_path):
    space = _phase1_space(tmp_path)
    org = space / "org" / "next_actions.org"
    org.write_text(org.read_text() + CLOSED)
    import_space(space, actor="writer")
    ingest.sync_state(space, actor="writer")
    before = read_events(space)

    import_space(space, actor="writer")

    assert read_events(space) == before


def test_a_phase0_space_still_leaves_closed_history_out(tmp_path):
    """Authored (phase-0) files keep years of DONE history the ledger was never
    meant to import; nothing there refuses an unadmitted heading."""
    space = _phase1_space(tmp_path, marker="0")
    org = space / "org" / "next_actions.org"
    org.write_text(org.read_text() + CLOSED)

    assert "closed-before-admission" not in {p.get("id") for p in scan(space).importable}
