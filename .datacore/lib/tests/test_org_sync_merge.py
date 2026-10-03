"""A conflicting .org file is merged task by task on :ID: (owner, 2026-09-30:
"merge by task ID, that's why we have org-workspace").

`org_sync_merge.merge3(base, ours, theirs)` is the three-way merge a sync runs
when two hosts changed the same .org file. What it must hold:

  * one task per :ID: in the result -- never two copies of a task both hosts
    edited, and the result loads in org-workspace (which refuses duplicate IDs);
  * a field only one side changed takes that side's value; a field both sides
    changed differently keeps both where that loses nothing (tags: the union;
    body: both sides' lines) and otherwise keeps this host's value and NOTES
    the other one, so the conflict task can name it;
  * a recorded closure beats an open state (the rule org_union_merge applies);
  * a task only one side has is kept; a task one side removed and the other
    left alone is removed; one side removed and the other edited is kept;
  * a heading without an :ID: falls back to line union, for that heading only.
"""
from __future__ import annotations

import pytest

import org_sync_merge as m


def _parses(text: str, tmp_path) -> list[str]:
    """The ids org-workspace finds; raises if it refuses the file."""
    from org_workspace import OrgWorkspace
    f = tmp_path / "merged.org"
    f.write_text(text)
    ws = OrgWorkspace()
    ws.load(f)
    return [n.id() for n in ws.all_nodes() if n.id()]


BASE = """#+TITLE: fixture
* Projects
** TODO Write the report
   :PROPERTIES:
   :ID: task-1
   :OWNER: gregor
   :END:
   first body line
** TODO Call the bank
   :PROPERTIES:
   :ID: task-2
   :END:
"""


def _with(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new, 1)


def test_the_same_task_edited_on_both_sides_is_one_task_with_both_edits(tmp_path):
    ours = _with(BASE, "** TODO Write the report", "** NEXT Write the report")
    theirs = _with(BASE, "** TODO Write the report", "** TODO Write the report  :urgent:")

    res = m.merge3(BASE, ours, theirs)

    ids = _parses(res.text, tmp_path)
    assert ids.count("task-1") == 1 and ids.count("task-2") == 1, res.text
    assert res.text.count("Write the report") == 1, res.text
    head = next(l for l in res.text.splitlines() if "Write the report" in l)
    assert head.startswith("** NEXT") and ":urgent:" in head, head
    assert not res.notes and not res.needs_person, res.notes


def test_the_same_field_changed_differently_keeps_this_hosts_value_and_notes_the_other(tmp_path):
    ours = _with(BASE, "Write the report", "Write the Q3 report")
    theirs = _with(BASE, "Write the report", "Write the annual report")

    res = m.merge3(BASE, ours, theirs)

    assert _parses(res.text, tmp_path).count("task-1") == 1
    assert "Write the Q3 report" in res.text and "annual" not in res.text, res.text
    assert res.needs_person
    (note,) = res.notes
    assert note["id"] == "task-1" and note["field"] == "heading", note
    assert "annual" in note["theirs"] and "Q3" in note["ours"], note


def test_a_property_changed_differently_keeps_this_hosts_value_and_notes_the_other(tmp_path):
    ours = _with(BASE, ":OWNER: gregor", ":OWNER: miles")
    theirs = _with(BASE, ":OWNER: gregor", ":OWNER: winston")

    res = m.merge3(BASE, ours, theirs)

    assert _parses(res.text, tmp_path).count("task-1") == 1
    assert ":OWNER: miles" in res.text and "winston" not in res.text, res.text
    assert [n["field"] for n in res.notes] == ["property OWNER"], res.notes


def test_both_bodies_changed_keeps_both_sides_lines_once_each(tmp_path):
    ours = _with(BASE, "   first body line\n", "   first body line, said here\n")
    theirs = _with(BASE, "   first body line\n", "   first body line, said there\n")

    res = m.merge3(BASE, ours, theirs)

    assert _parses(res.text, tmp_path).count("task-1") == 1
    assert "said here" in res.text and "said there" in res.text, res.text
    assert res.needs_person                 # both lines stand side by side: a person tidies


def test_a_recorded_closure_beats_an_open_state(tmp_path):
    ours = _with(BASE, "** TODO Call the bank", "** NEXT Call the bank")
    theirs = _with(BASE, "** TODO Call the bank\n", "** DONE Call the bank\n   CLOSED: [2026-09-30 Wed 10:00]\n")

    res = m.merge3(BASE, ours, theirs)

    assert _parses(res.text, tmp_path).count("task-2") == 1
    assert "** DONE Call the bank" in res.text and "CLOSED: [2026-09-30 Wed 10:00]" in res.text, res.text
    assert not res.needs_person


def test_different_tasks_added_on_both_sides_are_each_present_once(tmp_path):
    ours = BASE + "** TODO Added here\n   :PROPERTIES:\n   :ID: task-ours\n   :END:\n"
    theirs = BASE + "** TODO Added there\n   :PROPERTIES:\n   :ID: task-theirs\n   :END:\n"

    res = m.merge3(BASE, ours, theirs)

    ids = _parses(res.text, tmp_path)
    for iid in ("task-1", "task-2", "task-ours", "task-theirs"):
        assert ids.count(iid) == 1, (iid, res.text)
    assert not res.needs_person and res.how == m.HOW, (res.notes, res.how)


def test_the_same_new_task_added_on_both_sides_is_one_task(tmp_path):
    new_o = "** TODO Fresh task\n   :PROPERTIES:\n   :ID: task-new\n   :END:\n   ours says\n"
    new_t = "** TODO Fresh task\n   :PROPERTIES:\n   :ID: task-new\n   :END:\n   theirs says\n"

    res = m.merge3(BASE, BASE + new_o, BASE + new_t)

    assert _parses(res.text, tmp_path).count("task-new") == 1, res.text
    assert "ours says" in res.text and "theirs says" in res.text


def test_a_task_removed_on_one_side_and_untouched_on_the_other_is_removed(tmp_path):
    theirs = BASE.split("** TODO Call the bank")[0]
    ours = _with(BASE, "first body line", "first body line, edited here")

    res = m.merge3(BASE, ours, theirs)

    assert "task-2" not in _parses(res.text, tmp_path)
    assert "edited here" in res.text


INBOX_BASE = """#+TITLE: Inbox
* Inbox
** TODO Daily digest
   :PROPERTIES:
   :ID: cap-1
   :END:
** TODO Reply to the bank
   :PROPERTIES:
   :ID: cap-2
   :END:
** TODO Read the article
   :PROPERTIES:
   :ID: cap-3
   :END:
"""
NEW_CAPTURE = "** TODO Review by hand\n   :PROPERTIES:\n   :ID: cap-new\n   :END:\n"


@pytest.mark.parametrize("processed_on", ["theirs", "ours"])
def test_tasks_processed_out_on_one_side_stay_out_when_the_other_appends_beside_them(tmp_path, processed_on):
    """0-personal, 2026-10-03 05:25 UTC: the inbox processor on one host moved
    twelve untouched captures out of inbox.org while the other host appended a
    capture right after them. In the one hunk both sides changed, every task of
    this host's side was kept, so the twelve came back; the merge then failed
    its own check and kept this host's whole copy. Every processed capture was
    back in the inbox AND in the file it had been moved to."""
    processed = "#+TITLE: Inbox\n* Inbox\n"
    appended = INBOX_BASE + NEW_CAPTURE
    ours, theirs = (appended, processed) if processed_on == "theirs" else (processed, appended)

    res = m.merge3(INBOX_BASE, ours, theirs)

    assert res.how == m.HOW, res.how                   # merged by id, not the fallback
    assert sorted(_parses(res.text, tmp_path)) == ["cap-new"], res.text
    assert not res.needs_person, res.notes


def test_a_task_removed_on_one_side_and_edited_on_the_other_is_kept(tmp_path):
    theirs = BASE.split("** TODO Call the bank")[0]
    ours = _with(BASE, "** TODO Call the bank", "** NEXT Call the bank")
    ours = _with(ours, "first body line", "first body line, edited here")

    res = m.merge3(BASE, ours, theirs)

    assert _parses(res.text, tmp_path).count("task-2") == 1
    assert "** NEXT Call the bank" in res.text and "edited here" in res.text
    assert res.how == m.HOW, res.how                   # merged by id, not the fallback
    assert [n["id"] for n in res.notes] == ["task-2"] and res.needs_person, res.notes


def test_a_task_moved_by_one_side_and_edited_by_the_other_is_one_task(tmp_path):
    base = BASE + "* Someday\n"
    block = ("** TODO Call the bank\n   :PROPERTIES:\n   :ID: task-2\n   :END:\n")
    theirs = base.replace(block, "") + block        # refiled under Someday
    ours = _with(base, "** TODO Call the bank", "** NEXT Call the bank")

    res = m.merge3(base, ours, theirs)

    assert _parses(res.text, tmp_path).count("task-2") == 1, res.text
    assert "** NEXT Call the bank" in res.text and res.how == m.HOW, res.how
    assert res.text.index("* Someday") < res.text.index("Call the bank"), res.text   # the move stands


def test_a_heading_without_an_id_falls_back_to_line_union(tmp_path):
    base = "* Shared note\nfirst version\n"
    ours = "* Shared note\nversion written on the always-on host\n"
    theirs = "* Shared note\nversion written on the executor host\n"

    res = m.merge3(base, ours, theirs)

    assert res.text.count("* Shared note") == 1, res.text
    assert "always-on host" in res.text and "executor host" in res.text
    assert "<<<<<<<" not in res.text
    assert res.needs_person
    _parses(res.text, tmp_path)


def test_a_side_org_workspace_refuses_is_left_as_this_hosts_copy_for_a_person(tmp_path):
    # Ours already carries a duplicate id (broken before the sync): there is no
    # identity to merge by, and a line union would carry the duplicate on. The
    # file stays exactly as this host had it; the other side is in history and
    # the conflict task names it.
    dup = BASE + "** TODO Twin\n   :PROPERTIES:\n   :ID: task-1\n   :END:\n"
    theirs = _with(BASE, "** TODO Call the bank", "** NEXT Call the bank")
    res = m.merge3(BASE, dup, theirs)
    assert res.text == dup
    assert res.needs_person and res.how.startswith("kept this host's copy"), res.how


def test_a_task_only_one_side_changed_keeps_that_sides_text_byte_for_byte(tmp_path):
    base = BASE.replace("** TODO Call the bank", "** TODO Call the bank    :money:")
    ours = _with(base, "** TODO Write the report", "** NEXT Write the report")
    theirs = _with(base, "** TODO Call the bank    :money:", "** WAITING Call the bank    :money:")

    res = m.merge3(base, ours, theirs)

    assert "** WAITING Call the bank    :money:\n" in res.text, res.text     # spacing untouched
    assert "** NEXT Write the report\n" in res.text
    assert not res.needs_person and res.how == m.HOW
