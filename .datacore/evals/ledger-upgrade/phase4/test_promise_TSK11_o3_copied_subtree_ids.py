"""O3 (deterministic): task identities are stable by construction (audit B-F6, B-F12).

Promise TSK-11; ledger upgrade Phase 4, eval O3, RE-STATED 2026-10-04 for owner
decision 8 ("one door in"), on a sandbox copy of a real Phase-1 space:

  * A COPIED SUBTREE IS CAPTURED, NOT A STOP. A person yanks a task's subtree,
    :ID: included, and pastes it below the original. After the cycles the space
    has not stopped; the copy is held as exactly one [NEEDS_REVIEW] inbox entry
    naming the original's id and carrying the copy; the original keeps its id
    and its ledger item is untouched; the regenerated view holds no duplicate
    id. (Before: the cycle refused the whole space, "duplicate Org IDs require
    explicit identity reconciliation".)
  * NO CYCLE REGENERATES AN ID THE LEDGER KNOWS. Over several cycles with
    ordinary edits, every id the ledger knows that is in the file stays on the
    same task, and the org-workspace library default refuses a duplicate
    instead of silently regenerating it (the 2026-08-11 churn of 1,204 ids).
"""
from __future__ import annotations

import pytest

import _ledger_drill as d


def test_a_copied_subtree_is_captured_and_the_original_keeps_its_id(sandbox):
    space = sandbox
    a = d.pick_items(space, 1)[0]
    original = d.state(space).items[a]
    title, payload_before = original.payload.get("title"), dict(original.payload)
    held0 = len(d.held_entries(space))
    known_before = set(d.state(space).items)

    text = d.read(space)
    s, e = d.block(text, a)
    copy = "\n".join(text.split("\n")[s:e])          # a plain yank: same heading, same :ID:
    d.write(space, d.insert_after(text, a, copy))

    cycles = [d.cycle(space), d.cycle(space)]
    stopped = [c for c in cycles if c.stopped]
    assert not stopped, f"copying a task as a template stopped the space: {stopped[0].reason}"

    ids = d.heading_order(d.read(space))
    assert len(ids) == len(set(ids)), "the regenerated view still holds a duplicate id"
    st = d.state(space)
    assert st.items[a].status != "dismissed" and st.items[a].payload.get("title") == title, \
        "the original task lost its identity or was closed"
    assert st.items[a].payload.get("org") == payload_before.get("org"), "the original's ledger item was rewritten"
    assert set(st.items) == known_before, "the copy was admitted to the ledger instead of being held for review"
    new = d.held_entries(space)[held0:]
    assert len(new) == 1 and new[0]["of"] == a and new[0]["needs_review"] and title in new[0]["text"], \
        f"the copy must be held as exactly one [NEEDS_REVIEW] inbox entry naming {a}; got {[(h['of'], h['change']) for h in new]}"


def test_no_cycle_regenerates_an_id_the_ledger_knows(sandbox, tmp_path):
    space = sandbox
    st = d.state(space)
    before = {i: (st.items[i].payload.get("title") or st.items[i].title)
              for i in d.heading_order(d.read(space)) if i in st.items}
    a, b = d.pick_items(space, 2)
    d.write(space, d.retitle(d.read(space), a, "Retitled between cycles"))
    for _ in range(3):
        c = d.cycle(space)
        assert not c.stopped, f"an ordinary retitle stopped the space: {c.reason}"
    after_ids = set(d.heading_order(d.read(space)))
    lost = sorted(set(before) - after_ids)
    assert not lost, f"ids the ledger knows vanished from the file over three cycles: {lost[:5]}"

    from org_workspace import OrgWorkspace
    text = d.read(space)
    s, e = d.block(text, b)
    dup = tmp_path / "with-duplicate.org"
    dup.write_text(d.insert_after(text, b, "\n".join(text.split("\n")[s:e])))
    with pytest.raises(Exception, match="(?i)duplicate"):
        OrgWorkspace().load(str(dup))   # the published default must refuse, never regenerate
