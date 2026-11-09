"""job_verify closes its repair task through the ledger, never by editing the
generated next_actions.org of a phase-1 space.

Live case (2026-10-01, the box): at 05:07 `_close_task` ran
`org_workspace_adapter.py complete` on 2-datacore's generated file. The edit
reached the ledger as a dismissal and moved the projection base to DONE. At
05:07:51 the inbox job's guard restored its 05:00 snapshot of that file, so the
file said TODO again while base and ledger said DONE, and every hourly import
from 06:00 refused it ("edit to a terminal item") until a hand repair at 17:30.

Closed through the ledger, the file and its base are untouched: a file revert
to the pre-close text is no edit at all, and the next projection renders the
task DONE from the ledger. The adapter path stays only where no ledger exists.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import job_verify  # noqa: E402
import ledger_ingest_org as ingest  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.projector import project  # noqa: E402
from ledger.projection_state import STATE, base_document  # noqa: E402
from ledger_project_org import project_space  # noqa: E402

TID = "fcd15291-bebe-48aa-a9fd-56379d005f27"


def _phase1_space(tmp_path: Path) -> Path:
    space = tmp_path / "2-datacore"
    (space / "org").mkdir(parents=True)
    (space / ".datacore" / "events").mkdir(parents=True)
    (space / ".datacore" / "ledger-phase").write_text("1\n")
    (space / ".datacore" / "ledger-edit-protocol").write_text("1\n")
    log = EventLog(space, "writer")
    for iid, title in (("other-task", "An unrelated open task"),
                       (TID, "job-verify: box-phase1-cycle is failing on box")):
        log.append("item.create", {
            "id": iid, "title": title, "state": "TODO", "space": space.name, "level": 1,
            "tags": ["datacore", "ops", "job_verify"],
            "org": {"body": "", "properties": {"JOB": "box-phase1-cycle"}, "priority": "B"}})
    text = project(fold(read_events(space)), space=space.name).text
    (space / "org" / "next_actions.org").write_text(text)
    (space / STATE).parent.mkdir(parents=True, exist_ok=True)
    (space / STATE).write_text(base_document(text))
    (space / "org" / "inbox.org").write_text("#+TITLE: Inbox\n")
    return space


def _heading(text: str, tid: str) -> str:
    lines = text.splitlines()
    k = next(i for i, l in enumerate(lines) if tid in l and ":ID:" in l)
    return next(lines[i] for i in range(k, -1, -1) if lines[i].startswith("*"))


def test_a_closed_repair_task_survives_a_file_revert_and_projects_done(tmp_path, monkeypatch):
    space = _phase1_space(tmp_path)
    na = space / "org" / "next_actions.org"
    monkeypatch.setattr(job_verify, "TASK_FILE", str(na))
    monkeypatch.setattr(job_verify, "_default_actor", lambda: "jv-tester")
    snapshot = na.read_text()                       # the inbox guard's 05:00 copy

    assert job_verify._close_task(TID) is True

    item = fold(read_events(space)).items[TID]
    assert item.status == "dismissed" and item.closed_kind == "done", item
    assert na.read_text() == snapshot, "the generated file is the projector's, not job_verify's"

    na.write_text(snapshot)                         # the guard restores its snapshot (05:07:51)
    ingest.sync_state(space, actor="writer")         # the hourly import: raised EditConflict before
    assert fold(read_events(space)).items["other-task"].status != "dismissed"

    out = project_space(space)
    assert not out.startswith("REFUSED"), out
    assert _heading(na.read_text(), TID).split()[1] == "DONE", na.read_text()
    assert _heading(na.read_text(), "other-task").split()[1] == "TODO"


def test_closing_twice_is_harmless(tmp_path, monkeypatch):
    space = _phase1_space(tmp_path)
    monkeypatch.setattr(job_verify, "TASK_FILE", str(space / "org" / "next_actions.org"))
    monkeypatch.setattr(job_verify, "_default_actor", lambda: "jv-tester")
    assert job_verify._close_task(TID) is True
    n = len(read_events(space))
    assert job_verify._close_task(TID) is True
    assert len(read_events(space)) == n, "an already-closed item gets no second dismissal"


def test_without_a_ledger_the_task_is_still_closed_in_the_file(tmp_path, monkeypatch):
    org = tmp_path / "0-personal" / "org"
    org.mkdir(parents=True)
    na = org / "next_actions.org"
    na.write_text(f"* Work\n** TODO job-verify: x is failing\n:PROPERTIES:\n:ID: {TID}\n:END:\n")
    monkeypatch.setattr(job_verify, "TASK_FILE", str(na))
    assert job_verify._close_task(TID) is True
    assert _heading(na.read_text(), TID).split()[1] == "DONE", na.read_text()
