"""A heading whose tag block the parser cannot read must not stop a space's ingest.

Org tags are `[A-Za-z0-9_@]`; one hyphen (`:ops:lp-ready:`) voids the whole
block, so the parser leaves it INSIDE the title and reports no tags. The
ingest admits such a heading exactly as parsed (title with the block, tags
empty). The projector, deliberately, never writes invalid org: it splits the
block out of the title and turns the hyphen into `_`.

So the file and the ledger's rendering of the SAME item read back differently,
and nobody edited anything. While the item is in the projection base the
three-way merge takes the ledger's side. But an item written into a generated
file after the base was last written has no base entry, and there the merge
calls the difference a concurrent edit -- the ingest and the projection both
refuse, every cycle, and the base can never advance. `snapshot()` must read
both sides the way the projection will render them.
"""
import sys
import time
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from ledger.fold import fold  # noqa: E402
from ledger.genesis import import_space  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.projector import project  # noqa: E402
from ledger.projection_state import base_document, reconcile, snapshot, STATE  # noqa: E402

HEADING = "* TODO [#A] Investigate the reset  :ops:trading:lp-ready:"


def _space(tmp_path):
    space = tmp_path / "9-fixture"
    (space / "org").mkdir(parents=True)
    (space / ".datacore").mkdir()
    (space / ".datacore/ledger-phase").write_text("1\n")
    return space


def test_snapshot_reads_an_invalid_block_as_the_projection_renders_it():
    text = HEADING + "\n  :PROPERTIES:\n  :ID: one\n  :END:\n"
    item = snapshot(text, "9-fixture")["items"]["one"]
    assert item["title"] == "Investigate the reset"
    assert item["tags"] == ["lp_ready", "ops", "trading"]
    assert item["effective_tags"] == ["lp_ready", "ops", "trading"]


def test_an_admitted_heading_with_an_invalid_block_reconciles_without_a_base(tmp_path):
    space = _space(tmp_path)
    # The base predates the heading: some other item, rendered earlier.
    EventLog(space, "writer").append("item.create", {
        "id": "older", "title": "Older task", "state": "TODO", "space": space.name,
        "level": 1, "tags": [], "org": {"body": "", "properties": {}, "priority": None}})
    older = project(fold(read_events(space)), space=space.name, as_of=time.time()).text
    (space / STATE).parent.mkdir(parents=True)
    (space / STATE).write_text(base_document(older))

    # A writer appends a heading straight into the generated file ...
    authored = older + HEADING + "\n  :PROPERTIES:\n  :ID: one\n  :END:\n"
    org = space / "org/next_actions.org"
    org.write_text(authored)
    # ... and the ingest admits it as parsed.
    import_space(space, org, actor="writer")
    state = fold(read_events(space))
    assert state.items["one"].payload["tags"] == []

    proposed = project(state, space=space.name, as_of=time.time()).text
    merged = reconcile(space, authored, proposed)  # raised EditConflict before

    assert merged["items"]["one"] == snapshot(proposed, space.name)["items"]["one"]
