"""A heading without a TODO keyword must not stop a space forever.

Three layers each had to be told the same thing, and each stopped the cycle at
a different point with an error naming a remedy that could not be followed:

  `cmd_ensure_ids`   gave ids only to headings with a todo state, so a plain
                     `* Someday` never got one -- and `projection_state.
                     snapshot()` refuses any file with an un-identified
                     heading: "ingest before projecting". Ingest was the thing
                     skipping it.
  `genesis.scan`     admitted a state-less heading only as an ANCESTOR of a
                     task being imported this run. One added above tasks the
                     ledger already knew, or with nothing under it, was never
                     admitted -- and the three-way merge then refused it as
                     "new heading is not admitted to the ledger; ingest first".
  `ensure_ids`       verified that every TASK had persisted an identity, so it
                     reported success on the file that was about to be refused.

Nothing clears any of these but editing a gitignored generated file by hand,
on every host, every hour, until someone notices.
"""
import sys
from argparse import Namespace
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import ledger_ingest_org as ingest  # noqa: E402
import org_workspace_adapter as adapter  # noqa: E402
from ledger.genesis import scan  # noqa: E402
from org_workspace import OrgWorkspace  # noqa: E402

PLAIN_AND_TASK = "* Someday\n** Maybe one day\n* TODO A real task\n"


def _space(tmp_path: Path) -> Path:
    space = tmp_path / "9-fixture"
    (space / ".datacore" / "events").mkdir(parents=True)
    (space / "org").mkdir(parents=True)
    (space / ".datacore" / "ledger-phase").write_text("1\n")
    return space


def _headings(path: Path):
    ws = OrgWorkspace()
    ws.load(str(path))
    return {n.heading: n.id() for n in ws.all_nodes()}


def test_every_heading_gets_an_identity_not_only_the_tasks(tmp_path):
    f = tmp_path / "next_actions.org"
    f.write_text(PLAIN_AND_TASK)

    adapter.cmd_ensure_ids(Namespace(file=str(f)))

    ids = _headings(f)
    assert set(ids) == {"Someday", "Maybe one day", "A real task"}
    assert all(ids.values()), ids
    assert len(set(ids.values())) == 3, "identities must be distinct"


def test_running_it_twice_changes_nothing(tmp_path):
    f = tmp_path / "next_actions.org"
    f.write_text(PLAIN_AND_TASK)
    adapter.cmd_ensure_ids(Namespace(file=str(f)))
    first = _headings(f)

    result = adapter.cmd_ensure_ids(Namespace(file=str(f)))

    assert result["added_count"] == 0
    assert _headings(f) == first


def test_the_ingest_check_notices_a_heading_left_without_one(tmp_path, monkeypatch):
    space = _space(tmp_path)
    (space / "org" / "next_actions.org").write_text(PLAIN_AND_TASK)
    # An adapter that still only does tasks: the state this fix replaced.
    monkeypatch.setattr(adapter, "cmd_ensure_ids", lambda args: {"added_count": 0, "nodes": []})

    try:
        ingest.ensure_ids(space)
    except RuntimeError as exc:
        assert "every heading identity" in str(exc)
    else:
        raise AssertionError("ensure_ids reported success on a file the projection refuses")


def test_a_plain_heading_is_admitted_even_with_no_task_under_it(tmp_path):
    space = _space(tmp_path)
    (space / "org" / "next_actions.org").write_text("* Someday\n* TODO A real task\n")
    ingest.ensure_ids(space)

    admitted = scan(space).importable

    someday = [p for p in admitted if p.get("title") == "Someday"]
    assert someday, [p.get("title") for p in admitted]
    assert someday[0]["section"] is True and someday[0]["state"] is None


def test_a_section_added_above_tasks_the_ledger_already_knows_is_admitted(tmp_path):
    # The ancestor walk only ran for tasks importable THIS run, so a heading
    # added over settled work was invisible to it.
    space = _space(tmp_path)
    org = space / "org" / "next_actions.org"
    org.write_text("* TODO A real task\n")
    ingest.ensure_ids(space)
    from ledger.genesis import import_space
    import_space(space, actor="fixture")
    assert not scan(space).importable, "the first import should have settled"

    org.write_text("* Later\n" + org.read_text())
    ingest.ensure_ids(space)

    admitted = scan(space).importable
    assert [p.get("title") for p in admitted] == ["Later"]


def test_a_section_is_offered_before_the_tasks_that_live_under_it(tmp_path):
    space = _space(tmp_path)
    (space / "org" / "next_actions.org").write_text("* Ops\n** TODO A real task\n")
    ingest.ensure_ids(space)

    headings = [p.get("title") for p in scan(space).importable]

    assert headings.index("Ops") < headings.index("A real task")
