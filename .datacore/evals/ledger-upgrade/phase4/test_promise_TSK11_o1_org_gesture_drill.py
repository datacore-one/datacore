"""O1 (deterministic drill): an ordinary edit to a task file never stops a space.

Promise TSK-11; ledger upgrade Phase 4, eval O1, RE-STATED 2026-10-04 for owner
decision 8 ("one door in": only inbox.org is written by hand; every other task
file is a view of the ledger). Replays the 13 everyday Emacs and Obsidian
gestures audit B (F3) tested, each on a fresh sandbox copy of a real Phase-1
space, and requires:

  1. THE SPACE KEEPS GOING. Neither of the two cycles after the edit stops the
     space (an ingest failure, or a refused projection).
  2. THE REST OF THE SPACE FLOWS. The same save retitles an unrelated task by
     hand, and an agent retitles a third task in the ledger; after the cycles
     the hand edit is in the ledger and the agent's edit is in the view.
  3. THE VIEW IS REGENERATED. After the cycles the view is exactly what the
     ledger last wrote (no edit left hanging in it).
  4. NO EDIT IS LOST. A PLAIN gesture (close, reopen, a note, a date, a
     retitle, a priority, a new task) reaches the ledger by the next cycle,
     and leaves no inbox entry. An UNCLEAR one (a removed heading, a copied
     subtree, a clash with an agent's edit, a reorder) either reaches the
     ledger, or is held as EXACTLY ONE inbox entry, marked [NEEDS_REVIEW],
     naming the task and carrying the human's text.

Gesture 5 is the audit's one row with three forms (delete, org-archive-subtree,
refile); each is checked.

    PROMISE_EVALS_ALL=1 pytest .datacore/evals/ledger-upgrade/phase4 -v
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

import _ledger_drill as d

STAMP = "[2026-10-04 Sun 10:00]"


def _close(text, item):
    text = d.set_state(text, item, "DONE")
    return d.edit_heading(text, item, lambda h: h + f"\nCLOSED: {STAMP}")


def _setup_closed(space, a):
    """Close A by hand and let a cycle take it in -- gestures 3 and 4 start here."""
    d.write(space, _close(d.read(space), a))
    c = d.cycle(space)
    if c.stopped or d.state(space).items[a].status != "dismissed":
        pytest.fail(f"SETUP: closing a task did not round-trip ({c.reason})")


def _new_heading(state_word, title, ident):
    return (f"* {state_word} {title}\n"
            + (f"CLOSED: {STAMP}\n" if state_word in ("DONE", "CANCELLED") else "")
            + f"  :PROPERTIES:\n  :ID: {ident}\n  :END:\n")


def _title(space, item):
    it = d.state(space).items[item]
    return it.payload.get("title") or it.title


def _org_props(space, item):
    return ((d.state(space).items[item].payload.get("org") or {}).get("properties") or {})


LIVE = ("created", "claimed", "granted", "completed")

# Each gesture: (setup, edit, applied, held) where
#   edit(space, text, a, b, ctx) -> text
#   applied(space, a, b, ctx)    -> (bool, what was observed)    the gesture reached the ledger
#   held = None for a PLAIN gesture, else (ids the entry may name, text it must carry)


def g1_edit(space, text, a, b, ctx):
    text = _close(text, a)
    return d.after_drawer(text, a, [":LOGBOOK:", f"- Note taken on {STAMP} \\\\", "  finished it", ":END:"])


def closed_done(space, a, b, ctx):
    it = d.state(space).items[a]
    return it.status == "dismissed" and it.closed_kind == "done", f"status={it.status} kind={it.closed_kind}"


def g2_edit(space, text, a, b, ctx):
    return d.after_drawer(_close(text, a), a, [":LOGBOOK:", ":END:"])


def g3_setup(space, a, b, ctx):
    _setup_closed(space, a)


def g3_edit(space, text, a, b, ctx):
    text = d.set_state(text, a, "TODO")
    lines = text.split("\n")
    s, e = d.block(text, a)
    lines[s:e] = [l for l in lines[s:e] if not l.startswith("CLOSED:")]
    return "\n".join(lines)


def reopened(space, a, b, ctx):
    it = d.state(space).items[a]
    text = d.read(space)
    shown = text.split("\n")[d.block(text, a)[0]] if a in text else "(not in view)"
    return it.status in LIVE and shown.startswith("* TODO"), f"ledger status={it.status}; view shows {shown[:40]!r}"


def g4_edit(space, text, a, b, ctx):
    return d.add_property(text, a, "OUTCOME", "done, and here is what came of it")


def outcome_noted(space, a, b, ctx):
    it = d.state(space).items[a]
    got = _org_props(space, a).get("OUTCOME")
    return got == "done, and here is what came of it" and it.status == "dismissed", \
        f"ledger OUTCOME={got!r} status={it.status}"


def gone(space, a, b, ctx):
    it = d.state(space).items[a]
    in_view = a in d.read(space)
    return it.status not in LIVE and not in_view, f"ledger status={it.status}; in the view={in_view}"


def g5a_edit(space, text, a, b, ctx):
    ctx["title"] = _title(space, a)
    return d.remove_block(text, a)[0]


def g5b_edit(space, text, a, b, ctx):
    ctx["title"] = _title(space, a)
    text, removed = d.remove_block(text, a)
    archived = removed.replace(f":ID: {a}", f":ID: {a}\n  :ARCHIVE_TIME: 2026-10-04 Sun 10:00\n  :ARCHIVE_FILE: {d.org(space)}")
    (space / "org" / "next_actions.org_archive").write_text(
        "#    -*- mode: org -*-\n\n\nArchived entries from file next_actions.org\n\n\n" + archived + "\n")
    return text


def g5c_edit(space, text, a, b, ctx):
    ctx["title"] = _title(space, a)
    text, removed = d.remove_block(text, a)
    (space / "org" / "someday.org").write_text("#+TITLE: Someday\n\n" + removed + "\n")
    return text


def g6_edit(space, text, a, b, ctx):
    return _close(d.add_property(text, a, "OUTCOME", "shipped"), a)


def outcome_and_closed(space, a, b, ctx):
    it = d.state(space).items[a]
    got = _org_props(space, a).get("OUTCOME")
    return it.status == "dismissed" and got == "shipped", f"status={it.status} OUTCOME={got!r}"


def g7_edit(space, text, a, b, ctx):
    ctx["new"] = str(uuid.uuid4())
    return d.insert_after(text, a, _new_heading("TODO", "A task typed in by hand", ctx["new"]))


def admitted_open(space, a, b, ctx):
    it = d.state(space).items.get(ctx["new"])
    return it is not None and it.status in LIVE, f"ledger item={'absent' if it is None else it.status}"


def g8_edit(space, text, a, b, ctx):
    ctx["new"] = str(uuid.uuid4())
    return d.insert_after(text, a, _new_heading("DONE", "Finished work, logged after the fact", ctx["new"]))


def admitted_done(space, a, b, ctx):
    it = d.state(space).items.get(ctx["new"])
    return (it is not None and it.status == "dismissed" and it.closed_kind == "done"), \
        f"ledger item={'absent' if it is None else (it.status, it.closed_kind)}"


def g9_edit(space, text, a, b, ctx):
    text = d.retitle(text, a, "Retitled by hand")
    text = d.edit_heading(text, a, lambda h: h.replace("[#B] ", "").replace("[#C] ", "").replace("[#A] ", "")
                          .replace(" Retitled by hand", " [#A] Retitled by hand", 1))
    return d.after_drawer(text, a, [":LOGBOOK:", f"CLOCK: [2026-10-04 Sun 09:00]--{STAMP} =>  1:00", ":END:"])


def retitled(space, a, b, ctx):
    it = d.state(space).items[a]
    prio = (it.payload.get("org") or {}).get("priority")
    return _title(space, a) == "Retitled by hand" and prio == "A", f"title={_title(space, a)!r} priority={prio!r}"


def g10_edit(space, text, a, b, ctx):
    d.agent_edit(space, a, {"title": "Retitled by the agent"})
    return d.retitle(text, a, "Retitled by the human")


def clash_settled(space, a, b, ctx):
    # Applied would mean the ledger carries the human's title AND the agent's is not lost; there is
    # no deterministic way to do both, so this gesture can only pass by being held.
    return False, f"ledger title {_title(space, a)!r}"


def g11_edit(space, text, a, b, ctx):
    tags = sorted(set(d.state(space).items[a].payload.get("tags") or []) | {"agenttag"})
    d.agent_edit(space, a, {"tags": tags})
    return d.append_body(text, a, ["     A line the human added to the body."])


def merged(space, a, b, ctx):
    it = d.state(space).items[a]
    body = (it.payload.get("org") or {}).get("body") or ""
    ok = "A line the human added to the body." in body and "agenttag" in (it.payload.get("tags") or [])
    return ok, f"tags={it.payload.get('tags')} body has line={'A line the human' in body}"


def g12_edit(space, text, a, b, ctx):
    s, e = d.block(text, a)
    copy = "\n".join(text.split("\n")[s:e])
    ctx["orig_title"] = _title(space, a)
    copy = d.retitle(copy, a, "Template copy of a task")
    return d.insert_after(text, a, copy)


def copy_reminted(space, a, b, ctx):
    st = d.state(space)
    copies = [i for i in st.items.values()
              if (i.payload.get("title") or i.title) == "Template copy of a task" and i.status in LIVE]
    ok = _title(space, a) == ctx["orig_title"] and len(copies) == 1 and copies[0].id != a
    return ok, f"original title kept={_title(space, a) == ctx['orig_title']}, live copies with a new id={len(copies)}"


def g13_edit(space, text, a, b, ctx):
    return d.swap(text, a, b)


def order_kept(space, a, b, ctx):
    order = d.heading_order(d.read(space))
    kept = a in order and b in order and order.index(b) < order.index(a)
    return kept, "hand order kept" if kept else "hand order not in the regenerated view"


def _a(a, b, ctx):
    return {a}


GESTURES = {
    "01-close-with-logbook-note": (None, g1_edit, closed_done, None),
    "02-close-with-empty-logbook": (None, g2_edit, closed_done, None),
    "03-reopen-done-task": (g3_setup, g3_edit, reopened, None),
    "04-note-on-closed-task": (g3_setup, g4_edit, outcome_noted, None),
    "05a-delete-heading": (None, g5a_edit, gone, (_a, lambda ctx: ctx["title"])),
    "05b-archive-subtree": (None, g5b_edit, gone, (_a, lambda ctx: ctx["title"])),
    "05c-refile-to-other-file": (None, g5c_edit, gone, (_a, lambda ctx: ctx["title"])),
    "06-outcome-then-close": (None, g6_edit, outcome_and_closed, None),
    "07-new-todo-heading": (None, g7_edit, admitted_open, None),
    "08-new-heading-typed-done": (None, g8_edit, admitted_done, None),
    "09-retitle-reprioritise-clock": (None, g9_edit, retitled, None),
    "10-human-and-agent-retitle": (None, g10_edit, clash_settled, (_a, lambda ctx: "Retitled by the human")),
    "11-human-body-agent-tags": (None, g11_edit, merged, None),
    "12-copy-as-template": (None, g12_edit, copy_reminted, (_a, lambda ctx: "Template copy of a task")),
    "13-manual-reorder": (None, g13_edit, order_kept, (lambda a, b, ctx: {a, b}, lambda ctx: "")),
}


def _record(gesture, outcome, detail):
    out = os.environ.get("LEDGER_DRILL_OUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps({"gesture": gesture, "outcome": outcome, "detail": detail}) + "\n")


@pytest.mark.parametrize("gesture", list(GESTURES))
def test_gesture_never_stops_the_space(sandbox, gesture):
    space = sandbox
    setup, edit, applied, held = GESTURES[gesture]
    a, b, human_bystander, agent_bystander = d.pick_items(space, 4)
    ctx: dict = {}
    if setup:
        setup(space, a, b, ctx)
    entries_before = len(d.held_entries(space))

    text = edit(space, d.read(space), a, b, ctx)
    text = d.retitle(text, human_bystander, "Bystander, retitled by hand in the same save")
    d.write(space, text)
    d.agent_edit(space, agent_bystander, {"title": "Bystander, retitled by an agent"})

    first = d.cycle(space)
    applied_by_next_cycle, seen_first = (applied(space, a, b, ctx) if not first.stopped else (False, first.reason))
    second = d.cycle(space)
    stopped = [c for c in (first, second) if c.stopped]
    if stopped:
        _record(gesture, "STOPS SPACE", stopped[0].reason)
    assert not stopped, f"[{gesture}] the space STOPPED: {stopped[0].reason}"

    hand = _title(space, human_bystander)
    shown = "Bystander, retitled by an agent" in d.read(space)
    if hand != "Bystander, retitled by hand in the same save" or not shown:
        _record(gesture, "REST OF SPACE STALLED", f"hand edit in ledger={hand!r}; agent edit in view={shown}")
    assert hand == "Bystander, retitled by hand in the same save", \
        f"[{gesture}] an unrelated hand edit in the same save never reached the ledger ({hand!r})"
    assert shown, f"[{gesture}] an unrelated agent edit never reached the view"

    regenerated = d.read(space) == d.base_text(space)
    if not regenerated:
        _record(gesture, "VIEW NOT REGENERATED", "the view differs from what the ledger last wrote")
    assert regenerated, f"[{gesture}] the view was not regenerated: it differs from what the ledger last wrote"

    new = d.held_entries(space)[entries_before:]
    ok, observed = applied(space, a, b, ctx)
    if held is None:
        outcome = "APPLIED" if applied_by_next_cycle and not new else (
            "HELD (plain gesture must be applied)" if new else "NOT APPLIED")
        _record(gesture, outcome, seen_first if not applied_by_next_cycle else observed)
        assert applied_by_next_cycle, f"[{gesture}] a plain edit did not reach the ledger by the next cycle: {seen_first}"
        assert not new, f"[{gesture}] a plain edit that was applied also left {len(new)} inbox entr(y/ies)"
        return

    may_name, must_carry = held
    names, carry = may_name(a, b, ctx), must_carry(ctx)
    if ok and not new:
        _record(gesture, "APPLIED", observed)
        return
    good = (len(new) == 1 and new[0]["of"] in names and new[0]["needs_review"]
            and carry in new[0]["text"])
    _record(gesture, "HELD (one inbox entry)" if good else
            f"NEITHER APPLIED NOR HELD ONCE ({len(new)} new entries)", observed)
    assert good, (f"[{gesture}] neither reached the ledger ({observed}) nor is held as exactly one "
                  f"[NEEDS_REVIEW] inbox entry naming the task and carrying the edit; new entries: "
                  f"{[(e['of'], e['change']) for e in new]}")
