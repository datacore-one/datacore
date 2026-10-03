"""O3 (deterministic): task identities are stable by construction (audit B-F6, B-F12).

Ledger upgrade Phase 4, eval O3 (PLAN.md), on a sandbox copy of a real Phase-1
space:

  * COPY AS TEMPLATE RE-MINTS. A person yanks a task's subtree, :ID: included,
    and pastes it below the original. After the cycles the original keeps its
    id and its ledger item is untouched; the copy is a new live item with an id
    the ledger had never seen; the file holds no duplicate id. (Today the cycle
    refuses the whole space: "duplicate Org IDs require explicit identity
    reconciliation".)
  * NO CYCLE REGENERATES AN ID THE LEDGER KNOWS. Over several cycles with
    ordinary edits, every id the ledger knows that is in the file stays on the
    same task, and the org-workspace library default refuses a duplicate
    instead of silently regenerating it (the 2026-08-11 churn of 1,204 ids).
"""
from __future__ import annotations

import pytest

import _ledger_drill as d


def test_a_copied_subtree_is_reminted_and_the_original_keeps_its_id(sandbox):
    space = sandbox
    a = d.pick_items(space, 1)[0]
    known_before = set(d.state(space).items)
    original = d.state(space).items[a]
    title, payload_before = original.payload.get("title"), dict(original.payload)

    text = d.read(space)
    s, e = d.block(text, a)
    copy = "\n".join(text.split("\n")[s:e])          # a plain yank: same heading, same :ID:
    d.write(space, d.insert_after(text, a, copy))

    cycles = [d.cycle(space), d.cycle(space)]
    stopped = [c for c in cycles if c.stopped]
    assert not stopped, f"copying a task as a template stopped the space: {stopped[0].reason}"

    ids = d.heading_order(d.read(space))
    assert len(ids) == len(set(ids)), "the file still holds a duplicate id after the cycles"
    st = d.state(space)
    assert st.items[a].status != "dismissed" and st.items[a].payload.get("title") == title, \
        "the original task lost its identity or was closed"
    assert st.items[a].payload.get("org") == payload_before.get("org"), "the original's ledger item was rewritten"
    fresh = [i for i in st.items.values()
             if i.id not in known_before and (i.payload.get("title") or i.title) == title and i.status != "dismissed"]
    assert len(fresh) == 1, f"expected one new live item for the copy with an id the ledger never saw; found {len(fresh)}"


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
