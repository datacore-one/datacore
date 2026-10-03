"""O2 (deterministic): a human edit never stalls ingest (E-D1, audit E section 3.4).

Ledger upgrade Phase 4, eval O2 (PLAN.md). On a sandbox copy of a real Phase-1
space, after a clean ingest and projection, a person saves one edit to the
generated file that

  * retitles one task,
  * adds a property to another,
  * swaps two tasks by hand, and
  * retitles a fifth task that an agent also retitles in the ledger.

Then three more cycles run. Required:

  1. the projection base (last-rendered.json) advances within 2 cycles and is
     stable after that -- the space is not stuck re-deriving the same state;
  2. the one real conflict (the fifth task) is recorded EXACTLY ONCE, and no
     later cycle records it again;
  3. every non-conflicting human edit is in the ledger (title, property) or the
     file (the hand order);
  4. the sandbox ledger shows no new invariant finding.

Seeded failure: "the refusal leaves the base un-advanced" (chaos drill scenario
EDIT BETWEEN INGEST AND PROJECT) -- the conflict is then re-derived on every
cycle and nothing else moves. That is today's behaviour, so this is red.
"""
from __future__ import annotations

import hashlib
import io
import json
from contextlib import redirect_stdout

import _ledger_drill as d


def _sha(text):
    return hashlib.sha256((text or "").encode()).hexdigest()[:12]


def _conflicts_for(space, item):
    """How many times a conflict on `item` is recorded: retained edit conflicts in the ledger."""
    return len(d.state(space).items[item].edit_conflicts or {})


def _invariants(root):
    import ledger_invariants
    buf = io.StringIO()
    with redirect_stdout(buf):
        ledger_invariants.main(["--root", str(root), "--quick", "--json"])
    try:
        doc = json.loads(buf.getvalue())
    except ValueError:
        return {"unparsed": buf.getvalue()[-300:]}
    return sorted(f"{f['invariant']}:{f['detail']}" for f in doc.get("broken", []))


def test_a_human_edit_advances_the_base_and_records_its_conflict_once(sandbox):
    space = sandbox
    a, b, c, e_, x = d.pick_items(space, 5)
    findings_before = _invariants(space.parent)
    base0 = _sha(d.base_text(space))

    text = d.read(space)
    text = d.retitle(text, a, "Retitled in Emacs")
    text = d.add_property(text, b, "CONTEXT", "@home")
    text = d.swap(text, c, e_)
    text = d.retitle(text, x, "Retitled by the human")
    d.write(space, text)
    d.agent_edit(space, x, {"title": "Retitled by the agent"})

    trail = []
    for n in (1, 2, 3):
        cyc = d.cycle(space)
        trail.append({"cycle": n, "base": _sha(d.base_text(space)), "conflicts_on_x": _conflicts_for(space, x),
                      "stopped": cyc.reason if cyc.stopped else None})
    summary = "; ".join(f"cycle {t['cycle']}: base {t['base']}, conflicts recorded {t['conflicts_on_x']}, "
                        f"{'STOPPED (' + t['stopped'] + ')' if t['stopped'] else 'ran'}" for t in trail)

    advanced_by_2 = trail[1]["base"] != base0
    stable_after = trail[2]["base"] == trail[1]["base"]
    assert advanced_by_2 and stable_after, f"the projection base did not advance within 2 cycles and settle — {summary}"

    counts = [t["conflicts_on_x"] for t in trail]
    assert counts == [1, 1, 1], f"the conflict on the doubly-edited task must be recorded exactly once, " \
                                f"and stay once; recorded per cycle: {counts} — {summary}"

    st = d.state(space)
    assert (st.items[a].payload.get("title")) == "Retitled in Emacs", "the hand retitle never reached the ledger"
    props = (st.items[b].payload.get("org") or {}).get("properties") or {}
    assert props.get("CONTEXT") == "@home", "the hand-added property never reached the ledger"
    order = d.heading_order(d.read(space))
    assert order.index(e_) < order.index(c), "the hand order was undone"

    assert _invariants(space.parent) == findings_before, "the cycles left a new ledger invariant finding"
