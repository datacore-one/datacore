"""Phase F (tasks and ledger rebuild plan, 2026-10-31): one way in.

In a space whose `.datacore/config.yaml` sets `ledger: {org_lists: readonly}`:
  * every org list except inbox.org is a generated, read-only projection
    (GENERATED header, file mode without write bits);
  * the adapter writes task changes as ledger events and regenerates the
    projection; it never edits or derives from the projection's text;
  * a hand edit to the projection is turned into adapter commands by the
    editor hook (`org_readonly.py hook`), or refused with the command to use,
    and a refused edit is saved to a file the message names;
  * the trial checker compares the projection's tasks with the ledger's and
    reports lost or doubled tasks on one machine-readable last line.

The setting is OFF by default: a space without it behaves exactly as Phase 1.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
ADAPTER = LIB / "org_workspace_adapter.py"
PROJECT = LIB / "ledger_project_org.py"
READONLY = LIB / "org_readonly.py"
SPACE_HOOK = LIB.parent / "hooks" / "space-pre-commit"
sys.path.insert(0, str(LIB))


def _run(*args, cwd=None):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True,
                          timeout=300, cwd=cwd)


def _adapter(*args):
    r = _run(ADAPTER, *args)
    return r, (json.loads(r.stdout) if r.stdout.strip().startswith("{") else {})


def _phase1_space(tmp_path, name="9-trialspace"):
    space = tmp_path / name
    (space / ".datacore" / "events").mkdir(parents=True)
    (space / "org").mkdir()
    (space / ".datacore" / "ledger-phase").write_text("1\n")
    (space / ".datacore" / "ledger-edit-protocol").write_text("1\n")
    (space / ".datacore" / "config.yaml").write_text("space:\n  name: trialspace\n  type: team\n")
    (space / "org" / "inbox.org").write_text("#+TITLE: Inbox\n")
    subprocess.run(["git", "init", "-q", "."], cwd=space, capture_output=True)
    r = _run(PROJECT, "--root", tmp_path, "--space", space.name)
    assert r.returncode == 0 and "generated" in r.stdout, r.stdout + r.stderr
    ids = []
    for heading in ("First task", "Second task"):
        r, out = _adapter("add", "--file", space / "org" / "inbox.org", "--heading", heading)
        assert r.returncode == 0, r.stdout + r.stderr
        ids.append(out["id"])
        r, out = _adapter("move", "--from", space / "org" / "inbox.org",
                          "--to", space / "org" / "next_actions.org", "--id", ids[-1])
        assert r.returncode == 0, r.stdout + r.stderr
    r = _run(PROJECT, "--root", tmp_path, "--space", space.name)
    assert r.returncode == 0 and "generated" in r.stdout, r.stdout + r.stderr
    return space, ids


def _readonly_space(tmp_path, name="9-trialspace"):
    space, ids = _phase1_space(tmp_path, name)
    r = _run(READONLY, "on", "--space", space)
    assert r.returncode == 0, r.stdout + r.stderr
    return space, ids


def _ledger_item(space, tid):
    from ledger.hub import authoritative_state
    return authoritative_state(space).items.get(tid)


def _event_count(space):
    from ledger.log import read_events
    return len(list(read_events(space)))


def _writable(path):
    return bool(path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _hand_edit(path, old, new):
    """What an editor does after `chmod u+w`: replace the file's text."""
    os.chmod(path, 0o644)
    text = path.read_text()
    assert old in text, text
    path.write_text(text.replace(old, new, 1))


def _split(text):
    """(preamble, {title: block}) of a projection; blocks end with a newline."""
    import re
    parts = re.split(r"(?m)^(?=\* )", text)
    blocks = {}
    for part in parts[1:]:
        title = part.splitlines()[0].split(" ", 2)[2]
        blocks[title] = part if part.endswith("\n") else part + "\n"
    return parts[0], blocks


# -- the setting -------------------------------------------------------------

def test_off_by_default_the_projection_stays_phase1(tmp_path):
    space, _ = _phase1_space(tmp_path)
    import org_readonly
    assert org_readonly.enabled(space) is False
    generated = space / "org" / "next_actions.org"
    assert _writable(generated)
    assert "ingested hourly" in generated.read_text()


def test_switch_on_makes_the_projection_read_only_and_off_reverses_it(tmp_path):
    space, _ = _readonly_space(tmp_path)
    import org_readonly
    generated = space / "org" / "next_actions.org"
    assert org_readonly.enabled(space) is True
    assert "org_lists: readonly" in (space / ".datacore" / "config.yaml").read_text()
    assert not _writable(generated), oct(generated.stat().st_mode)
    head = generated.read_text().splitlines()[:6]
    assert any("GENERATED" in line and "READ-ONLY" in line for line in head), head
    assert "ingested hourly" not in generated.read_text()

    r = _run(READONLY, "off", "--space", space)
    assert r.returncode == 0, r.stdout + r.stderr
    assert org_readonly.enabled(space) is False
    assert "org_lists" not in (space / ".datacore" / "config.yaml").read_text()
    assert "name: trialspace" in (space / ".datacore" / "config.yaml").read_text()
    assert _writable(generated)
    assert "ingested hourly" in generated.read_text()


def test_switch_on_refuses_a_space_with_a_list_the_ledger_cannot_generate(tmp_path):
    space, _ = _phase1_space(tmp_path)
    (space / "org" / "someday.org").write_text("* TODO Maybe one day\n")
    r = _run(READONLY, "on", "--space", space)
    assert r.returncode != 0
    assert "someday.org" in r.stdout + r.stderr
    import org_readonly
    assert org_readonly.enabled(space) is False


# -- the adapter writes events, then regenerates ----------------------------

def test_adapter_update_writes_an_event_and_regenerates_the_read_only_list(tmp_path):
    space, (first, second) = _readonly_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    r, out = _adapter("update", "--file", generated, "--id", first, "--state", "NEXT",
                      "--scheduled", "2026-11-20")
    assert r.returncode == 0, r.stdout + r.stderr
    item = _ledger_item(space, first)
    assert item.payload["state"] == "NEXT"
    assert item.payload["scheduled"]
    text = generated.read_text()
    assert "* NEXT First task" in text
    assert "SCHEDULED: <2026-11-20" in text
    assert not _writable(generated)
    assert out["observed"]["STATE"] == "NEXT"


def test_adapter_never_derives_events_from_the_projection_text(tmp_path):
    """A pending hand edit on another task must not ride along with an adapter write."""
    space, (first, second) = _readonly_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    _hand_edit(generated, "TODO Second task", "WAITING Second task")
    r, _ = _adapter("update", "--file", generated, "--id", first, "--state", "NEXT")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ledger_item(space, first).payload["state"] == "NEXT"
    assert _ledger_item(space, second).payload["state"] == "TODO", \
        "the adapter turned an unrelated hand edit in the projection into an event"


def test_adapter_complete_dismisses_in_the_ledger(tmp_path):
    space, (first, _) = _readonly_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    r, out = _adapter("complete", "--file", generated, "--id", first)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ledger_item(space, first).status == "dismissed"
    assert "* DONE First task" in generated.read_text()
    assert not _writable(generated)


def test_adapter_update_sets_a_deadline(tmp_path):
    space, (first, _) = _readonly_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    r, _ = _adapter("update", "--file", generated, "--id", first, "--deadline", "2026-12-01")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ledger_item(space, first).payload["deadline"]
    assert "DEADLINE: <2026-12-01" in generated.read_text()


def test_adapter_add_into_the_read_only_list_creates_a_top_level_item(tmp_path):
    space, _ = _readonly_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    r, out = _adapter("add", "--file", generated, "--allow-any-file", "--heading", "Third task",
                      "--state", "NEXT")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ledger_item(space, out["id"]) is not None
    assert "\n* NEXT Third task" in generated.read_text()
    assert not _writable(generated)


def test_a_direct_write_to_the_read_only_list_is_refused_with_the_command(tmp_path):
    space, (first, _) = _readonly_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    from org_transaction import serialized, watch_file, write_org_text

    @serialized
    def scribble():
        watch_file(generated)
        write_org_text(generated, generated.read_text().replace("First task", "Scribbled"))

    with pytest.raises(PermissionError) as info:
        scribble()
    assert "org_workspace_adapter.py" in str(info.value)
    assert "First task" in generated.read_text()


def test_the_hourly_ingest_does_not_take_a_hand_edit_in_a_read_only_space(tmp_path):
    space, (first, _) = _readonly_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    _hand_edit(generated, "TODO First task", "DONE First task")
    before = _event_count(space)
    from ledger_ingest_org import sync_state
    result = sync_state(space)
    assert result.get("readonly") is True
    assert _event_count(space) == before
    assert _ledger_item(space, first).status != "dismissed"


# -- the editor hook ---------------------------------------------------------

def test_hook_turns_recognisable_edits_into_adapter_commands(tmp_path):
    space, (first, second) = _readonly_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    _hand_edit(generated, "TODO First task", "DONE First task")
    _hand_edit(generated, "TODO Second task", "NEXT Second task\nSCHEDULED: <2026-11-10 Tue> DEADLINE: <2026-11-12 Thu>")
    text = generated.read_text().rstrip("\n") + "\n\n* TODO A new hand-typed task\n"
    generated.write_text(text)
    before = _event_count(space)

    dry = _run(READONLY, "hook", "--space", space)
    assert dry.returncode == 2, dry.stdout + dry.stderr   # refusal for new task
    assert _event_count(space) == before
    for needle in (f"update --file {generated} --id {first} --state DONE",
                   f"--id {second} --state NEXT",
                   "--scheduled 2026-11-10", "--deadline 2026-11-12",
                   "add --file", "A new hand-typed task"):
        assert needle in dry.stdout, (needle, dry.stdout)
    assert "inbox.org" in dry.stdout, "refusal must suggest adding to the inbox"

    done = _run(READONLY, "hook", "--space", space, "--apply")
    assert done.returncode == 2, done.stdout + done.stderr  # refusal; state changes still applied
    assert _ledger_item(space, first).status == "dismissed"
    item = _ledger_item(space, second)
    assert item.payload["state"] == "NEXT"
    assert item.payload["scheduled"]
    assert item.payload["deadline"]
    from ledger.hub import authoritative_state
    titles = [i.payload.get("title") for i in authoritative_state(space).items.values()]
    assert titles.count("A new hand-typed task") == 0, \
        "new tasks must not be auto-added to the ledger; they go via the inbox"
    assert not _writable(generated)
    clean = _run(READONLY, "hook", "--space", space)
    assert clean.returncode == 0, clean.stdout + clean.stderr


def test_hook_refuses_an_unrecognised_edit_saves_it_and_names_the_command(tmp_path):
    space, (first, second) = _readonly_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    _hand_edit(generated, "TODO First task", "TODO First task, renamed")
    _hand_edit(generated, "TODO Second task", "NEXT Second task")
    edited = generated.read_text()

    r = _run(READONLY, "hook", "--space", space, "--apply")
    assert r.returncode == 2, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "REFUSED" in out
    assert f"update --file {generated} --id {first} --heading" in out
    saved = [line.split("saved to ", 1)[1].split()[0] for line in out.splitlines() if "saved to " in line]
    assert saved, out
    assert Path(saved[0]).read_text() == edited, "the refused edit was not kept verbatim"
    # The recognisable part went through; the refused part did not reach the ledger.
    assert _ledger_item(space, second).payload["state"] == "NEXT"
    assert _ledger_item(space, first).payload["title"] == "First task"
    # The list is the ledger's again, read-only.
    assert "First task, renamed" not in generated.read_text()
    assert not _writable(generated)


def test_hook_refuses_a_deleted_heading(tmp_path):
    space, (first, _) = _readonly_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    os.chmod(generated, 0o644)
    pre, blocks = _split(generated.read_text())
    generated.write_text(pre + blocks["Second task"])
    r = _run(READONLY, "hook", "--space", space)
    assert r.returncode == 2, r.stdout + r.stderr
    assert f"--id {first} --state CANCELLED" in r.stdout


def test_space_pre_commit_refuses_while_a_read_only_list_has_a_hand_edit(tmp_path):
    space, (first, _) = _readonly_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    _hand_edit(generated, "TODO First task", "DONE First task")
    (space / "journal").mkdir()
    (space / "journal" / "note.md").write_text("x\n")
    subprocess.run(["git", "add", "journal/note.md"], cwd=space, check=True)
    r = subprocess.run(["sh", str(SPACE_HOOK)], cwd=space, capture_output=True, text=True, timeout=300)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "org_readonly.py hook" in r.stdout + r.stderr


# -- the trial checker -------------------------------------------------------

def test_trial_check_is_ok_when_the_projection_matches_the_ledger(tmp_path):
    space, _ = _readonly_space(tmp_path)
    r = _run(READONLY, "trial-check", "--space", space)
    assert r.returncode == 0, r.stdout + r.stderr
    last = r.stdout.strip().splitlines()[-1]
    assert last.startswith("OK 9-trialspace 2 task(s), 0 lost, 0 doubled"), last


def test_trial_check_reports_a_lost_and_a_doubled_task(tmp_path):
    space, (first, second) = _readonly_space(tmp_path)
    generated = space / "org" / "next_actions.org"
    os.chmod(generated, 0o644)
    pre, blocks = _split(generated.read_text())
    generated.write_text(pre + blocks["Second task"] + "\n" + blocks["Second task"])
    r = _run(READONLY, "trial-check", "--space", space)
    assert r.returncode == 1, r.stdout + r.stderr
    last = r.stdout.strip().splitlines()[-1]
    assert last.startswith("FAIL 9-trialspace"), last
    assert "1 lost" in last and "1 doubled" in last, last
    assert first in r.stdout and second in r.stdout
