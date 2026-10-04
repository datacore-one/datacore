"""O2 (deterministic): a human edit to a view reaches the ledger, or is held once.

Promise TSK-11; ledger upgrade Phase 4, eval O2 (E-D1, audit E section 3.4),
RE-STATED 2026-10-04 for owner decision 8 ("one door in"). On a sandbox copy of
a real Phase-1 space, after a clean cycle, a person saves one edit to the view
that

  * retitles one task,
  * adds a property to another,
  * swaps two tasks by hand, and
  * retitles a fifth task that an agent also retitles in the ledger.

Then three cycles run. Required:

  1. no cycle stops the space;
  2. the plain edits (the retitle, the property) are in the ledger within 2
     cycles;
  3. the swap is in the regenerated view, or held as exactly one inbox entry;
  4. the clash is held as exactly ONE [NEEDS_REVIEW] inbox entry carrying the
     human's title, recorded once and never again by later cycles, and the
     agent's title stands in the ledger;
  5. the view is regenerated within 2 cycles (it equals what the ledger last
     wrote) and stays put after that;
  6. the sandbox ledger shows no new broken invariant (the sandbox is not a git
     repository, so "unforked" is could-not-tell on both sides and only broken
     findings are compared).

Seeded failure: "the refusal leaves the base un-advanced" (chaos drill scenario
EDIT BETWEEN INGEST AND PROJECT) -- nothing moves and nothing is held.
"""
from __future__ import annotations

import hashlib
import io
import json
from contextlib import redirect_stdout

import _ledger_drill as d


def _sha(text):
    return hashlib.sha256((text or "").encode()).hexdigest()[:12]


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


def test_a_human_edit_reaches_the_ledger_or_is_held_exactly_once(sandbox):
    space = sandbox
    a, b, c, e_, x = d.pick_items(space, 5)
    findings_before = _invariants(space.parent)
    base0 = _sha(d.base_text(space))
    held0 = len(d.held_entries(space))

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
        new = d.held_entries(space)[held0:]
        trail.append({"cycle": n, "base": _sha(d.base_text(space)),
                      "regenerated": d.read(space) == d.base_text(space),
                      "held_for_x": sum(1 for h in new if h["of"] == x),
                      "held_for_swap": sum(1 for h in new if h["of"] in (c, e_)),
                      "stopped": cyc.reason if cyc.stopped else None})
    summary = "; ".join(
        f"cycle {t['cycle']}: base {t['base']}, view regenerated {t['regenerated']}, "
        f"held for the clash {t['held_for_x']}, "
        f"{'STOPPED (' + t['stopped'] + ')' if t['stopped'] else 'ran'}" for t in trail)

    assert not any(t["stopped"] for t in trail), f"a cycle stopped the space — {summary}"
    st = d.state(space)
    assert st.items[a].payload.get("title") == "Retitled in Emacs", f"the hand retitle never reached the ledger — {summary}"
    props = (st.items[b].payload.get("org") or {}).get("properties") or {}
    assert props.get("CONTEXT") == "@home", f"the hand-added property never reached the ledger — {summary}"

    assert [t["held_for_x"] for t in trail] == [1, 1, 1], \
        f"the clash must be held as exactly one inbox entry, recorded once and never again — {summary}"
    entry = [h for h in d.held_entries(space)[held0:] if h["of"] == x][0]
    assert entry["needs_review"] and "Retitled by the human" in entry["text"], \
        "the held clash is not marked [NEEDS_REVIEW] or lost the human's title"
    assert st.items[x].payload.get("title") == "Retitled by the agent", "the agent's edit did not stand"

    order = d.heading_order(d.read(space))
    swap_kept = order.index(e_) < order.index(c)
    assert swap_kept or trail[-1]["held_for_swap"] == 1, \
        f"the hand swap was neither kept nor held once (held {trail[-1]['held_for_swap']})"

    assert trail[1]["base"] != base0 and trail[1]["regenerated"] and trail[2]["regenerated"] \
        and trail[2]["base"] == trail[1]["base"], f"the view was not regenerated within 2 cycles and settled — {summary}"

    assert _invariants(space.parent) == findings_before, "the cycles left a new ledger invariant finding"
