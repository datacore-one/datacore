"""A task added to a Phase 1 space's GENERATED next_actions.org goes through the ledger.

Incident (nightshift, 2026-09-23 .. 09-27): a writer added a task straight into
the system space's generated org/next_actions.org with `org_workspace_adapter add
--allow-any-file`. The adapter wrote the heading in its own layout and appended
`item.create` -- but never moved the projection base. The file now held an item
the base had never seen. As soon as the ledger's copy of that item differed from
the file's (another host's write, or a layout the projector renders differently),
the three-way merge met "base absent, file present, ledger different" and
refused the whole space: "REFUSED -- concurrent edit at items.<id>". Nightshift
then stopped task admission for every space, every night.

The generated file is a projection. A task created there must be created in the
ledger and the file re-rendered FROM the ledger, so file and base agree with it.
"""
import json
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
ADAPTER = LIB / "org_workspace_adapter.py"
PROJECT = LIB / "ledger_project_org.py"
sys.path.insert(0, str(LIB))


def _run(*args):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, timeout=180)


def _phase1_space(tmp_path):
    space = tmp_path / "5-testspace"
    (space / ".datacore" / "events").mkdir(parents=True)
    (space / "org").mkdir()
    (space / ".datacore" / "ledger-phase").write_text("1\n")
    (space / ".datacore" / "ledger-edit-protocol").write_text("1\n")
    (space / "org" / "inbox.org").write_text("#+TITLE: Inbox\n")
    subprocess.run(["git", "init", "-q", "."], cwd=space, capture_output=True)
    # One task through the capture point, then a real projection: the space now
    # has a generated next_actions.org and a projection base.
    r = _run(ADAPTER, "add", "--file", space / "org" / "inbox.org", "--heading", "Existing task")
    assert r.returncode == 0, r.stdout + r.stderr
    r = _run(PROJECT, "--root", tmp_path, "--space", space.name)
    assert r.returncode == 0 and "generated" in r.stdout, r.stdout + r.stderr
    return space


def _project(tmp_path, space):
    return _run(PROJECT, "--root", tmp_path, "--space", space.name)


def test_add_into_generated_file_survives_a_concurrent_ledger_edit(tmp_path):
    space = _phase1_space(tmp_path)
    generated = space / "org" / "next_actions.org"

    r = _run(ADAPTER, "add", "--file", generated, "--allow-any-file",
             "--heading", "job-verify: some-job is failing on some-host (3 runs since 2026-09-23)",
             "--state", "TODO", "--tags", "datacore,ops,job_verify", "--priority", "B",
             "--property", "SURFACE=some-host", "--property", "JOB=some-job",
             "--body", "Recurring failure")
    assert r.returncode == 0, r.stdout + r.stderr
    tid = json.loads(r.stdout)["id"]

    # Another host changes the item in the ledger before this host projects.
    from ledger.edits import conditional_payload
    from ledger.fold import fold
    from ledger.log import EventLog, read_events
    item = fold(read_events(space)).items[tid]
    EventLog(space, "otherhost").append("item.update", conditional_payload(item, {"state": "WAITING"}, version=1))

    r = _project(tmp_path, space)
    assert r.returncode == 0 and "REFUSED" not in r.stdout, (
        "a task added to the generated file left the space unable to project: " + r.stdout + r.stderr)
    text = generated.read_text()
    assert tid in text
    assert "WAITING [#B] job-verify: some-job" in text, text


def test_add_into_generated_file_leaves_exactly_the_projection(tmp_path):
    space = _phase1_space(tmp_path)
    generated = space / "org" / "next_actions.org"

    r = _run(ADAPTER, "add", "--file", generated, "--allow-any-file",
             "--heading", "A clarified next action", "--state", "NEXT", "--property", "SURFACE=core")
    assert r.returncode == 0, r.stdout + r.stderr
    tid = json.loads(r.stdout)["id"]
    after_add = generated.read_text()

    # No parent was asked for: a generated file has no sections, so the task is
    # top level -- not nested under whatever task happens to come first.
    assert "\n* NEXT A clarified next action" in after_add, after_add

    # The file IS the projection: rendering again changes nothing.
    r = _project(tmp_path, space)
    assert r.returncode == 0 and "REFUSED" not in r.stdout, r.stdout + r.stderr
    assert generated.read_text() == after_add
    from ledger.fold import fold
    from ledger.log import read_events
    assert tid in fold(read_events(space)).items


# -- moving a clarified capture INTO the generated file admits it (owner, fleet
# console 2026-10-30, "build-admission"). The refusal left every single next
# action in the inbox night after night: the nightly inbox run had no way to file it.

def _projects_clean(tmp_path, space):
    generated = space / "org" / "next_actions.org"
    before = generated.read_text()
    r = _project(tmp_path, space)
    assert r.returncode == 0 and "REFUSED" not in r.stdout, r.stdout + r.stderr
    assert generated.read_text() == before


def test_a_captured_task_moved_into_the_generated_file_leaves_the_inbox(tmp_path):
    space = _phase1_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    inbox = space / "org" / "inbox.org"
    tid = json.loads(_run(ADAPTER, "add", "--file", inbox, "--heading", "Captured, now clarified").stdout)["id"]

    r = _run(ADAPTER, "move", "--from", inbox, "--to", generated, "--id", tid)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["moved"] is True
    assert tid not in inbox.read_text()
    assert generated.read_text().count(tid) == 1
    _projects_clean(tmp_path, space)


def test_a_hand_written_capture_is_created_in_the_ledger_when_moved(tmp_path):
    space = _phase1_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    inbox = space / "org" / "inbox.org"
    # Written by hand (or by a mail writer), so the ledger never heard of it, and
    # nested under the inbox's own section heading.
    inbox.write_text(inbox.read_text() + "* Inbox\n:PROPERTIES:\n:ID: inbox-section\n:END:\n"
                     "** TODO Email the auditor\n:PROPERTIES:\n:ID: hand-1\n:CREATED: [2026-10-29 Thu 09:00]\n:END:\n")

    r = _run(ADAPTER, "move", "--from", inbox, "--to", generated, "--id", "hand-1")
    assert r.returncode == 0, r.stdout + r.stderr
    from ledger.fold import fold
    from ledger.log import read_events
    item = fold(read_events(space)).items["hand-1"]
    assert item.status == "created"
    assert item.payload.get("parent") is None  # not nested under the inbox section
    assert "hand-1" not in inbox.read_text() and "Inbox" in inbox.read_text()
    assert "\n* TODO Email the auditor" in generated.read_text()
    _projects_clean(tmp_path, space)


def test_a_capture_already_closed_in_the_ledger_stays_in_the_inbox(tmp_path):
    space = _phase1_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    inbox = space / "org" / "inbox.org"
    tid = json.loads(_run(ADAPTER, "add", "--file", inbox, "--heading", "Already handled").stdout)["id"]
    from ledger.log import EventLog
    EventLog(space, "otherhost").append("item.dismiss", {"id": tid, "kind": "done", "reason": "test"})
    before = (inbox.read_text(), generated.read_text())

    r = _run(ADAPTER, "move", "--from", inbox, "--to", generated, "--id", tid)
    assert r.returncode != 0
    assert "closed" in json.loads(r.stdout)["error"]
    assert (inbox.read_text(), generated.read_text()) == before


def test_a_move_out_of_the_generated_file_is_still_refused(tmp_path):
    space = _phase1_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    from ledger.fold import fold
    from ledger.log import read_events
    tid = next(iter(fold(read_events(space)).items))
    before = generated.read_text()

    r = _run(ADAPTER, "move", "--from", generated, "--to", space / "org" / "someday.org", "--id", tid)
    assert r.returncode != 0
    assert "generated" in json.loads(r.stdout)["error"]
    assert generated.read_text() == before


def test_a_refusal_report_names_the_item_but_no_task_content(tmp_path):
    """Automation hears WHICH item to reconcile -- ledger ids only, never titles."""
    space = _phase1_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    from ledger.log import EventLog
    # The pre-fix shape of the incident: a heading in the generated file that the
    # ledger has never seen, carrying a title that must not leak into the report.
    generated.write_text(generated.read_text() + "* TODO Secret client title\n")
    r = _run(PROJECT, "--root", tmp_path, "--all", "--json")
    assert r.returncode == 1
    (result,) = json.loads(r.stdout)["spaces"]
    assert result["status"] == "refused"
    assert result["reason"] and "ingest" in result["reason"]
    assert "Secret" not in r.stdout

    # A concurrent edit names the ledger item.
    item_space = _phase1_space(tmp_path / "second")
    tid = json.loads(_run(ADAPTER, "add", "--file", item_space / "org" / "inbox.org",
                          "--heading", "Another secret title").stdout)["id"]
    _run(PROJECT, "--root", tmp_path / "second", "--space", item_space.name)
    target = item_space / "org" / "next_actions.org"
    target.write_text(target.read_text().replace("* TODO Another secret title", "* NEXT Another secret title"))
    from ledger.edits import conditional_payload
    from ledger.fold import fold
    from ledger.log import read_events
    item = fold(read_events(item_space)).items[tid]
    EventLog(item_space, "otherhost").append("item.update", conditional_payload(item, {"state": "WAITING"}, version=1))
    r = _run(PROJECT, "--root", tmp_path / "second", "--all", "--json")
    (result,) = json.loads(r.stdout)["spaces"]
    assert result["status"] == "refused"
    assert tid in result["reason"], result
    assert "secret" not in r.stdout.lower()
