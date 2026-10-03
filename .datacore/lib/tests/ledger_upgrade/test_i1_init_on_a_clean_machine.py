"""I1 (deterministic): on a clean machine, one init command makes a working space.

Ledger upgrade Phase 3, eval I1 (PLAN.md; audit C1, C4). On a clean machine
(`_install_kit.clean_machine`: empty HOME, empty data root holding a copy of
the code, no registry of this installation, no DATACORE_* variable):

    ledger_cli.py init --space team --actor alice

and then, WITHOUT any file edited by hand and without DATACORE_ACTOR or
--actor on the later commands, the space

  * appends: an item is created, claimed and completed as alice;
  * verifies: `verify` prints OK;
  * folds: `items` shows the item completed, owned by alice;

and the code tree is byte-for-byte what was installed (no Datacore file had to
be edited to get there). A second person (`principals add`) can be declared
the same way, and a second init on the same machine as someone else is
refused instead of overwriting the first person's identity.

Seeded failure: init missing (today: argparse "invalid choice"), or an init
that creates the folder but writes no identity, so the first append is refused
as an undeclared actor.
"""
from __future__ import annotations

import json

import pytest

import _install_kit as K


@pytest.fixture
def machine(tmp_path):
    return K.clean_machine(tmp_path)


def _ok(p, what):
    assert p.returncode == 0, (
        f"{what} failed on a clean machine (rc {p.returncode}):\n{p.stdout[-600:]}\n{p.stderr[-600:]}")
    return p


def test_init_then_append_verify_fold_with_nothing_edited_by_hand(machine):
    m = machine
    before = m.code_digest()
    _ok(m.cli("init --space team --actor alice"), "`ledger_cli.py init --space team --actor alice`")
    assert (m.root / "team").is_dir(), "init did not create the space folder"

    # No --actor and no DATACORE_ACTOR from here on: init must have declared alice.
    assert "DATACORE_ACTOR" not in m.env
    _ok(m.cli("""append --space team --type item.create --payload '{"id": "t-1", "title": "first task"}'"""),
        "the first append after init")
    _ok(m.cli("""append --space team --type item.claim --payload '{"id": "t-1"}'"""),
        "claiming the first task")
    _ok(m.cli("""append --space team --type item.complete --payload '{"id": "t-1"}'"""),
        "completing the first task")

    v = _ok(m.cli("verify --space team"), "`verify` after init")
    assert v.stdout.startswith("OK"), f"verify did not say OK: {v.stdout!r}"
    logs = sorted(p.name for p in (m.root / "team" / ".datacore" / "events").glob("*.jsonl"))
    assert logs == ["alice.jsonl"], f"the events were not filed under alice: {logs}"

    items = [json.loads(line) for line in _ok(m.cli("items --space team"), "`items`").stdout.splitlines()]
    item = next((i for i in items if i.get("id") == "t-1"), None)
    assert item is not None, f"the folded state does not show the task: {items}"
    assert item.get("status") == "completed", f"the completed task is not completed in the fold: {item}"
    assert item.get("owner") == "alice", f"the task is not alice's in the fold: {item}"

    assert m.code_digest() == before, "a file of Datacore's own code changed during the install"


def test_a_second_person_is_declared_by_command_and_init_never_overwrites_an_identity(machine):
    m = machine
    _ok(m.cli("init --space team --actor alice"), "init")
    _ok(m.cli("principals add --actor bob --kind human"), "`principals add --actor bob`")
    reg = (m.root / ".datacore" / "registry" / "principals.yaml").read_text()
    assert "alice" in reg and "bob" in reg, f"principals.yaml does not declare both people:\n{reg}"

    ident = m.home / ".datacore" / "identity.env"
    held = ident.read_text()
    p = m.cli("init --space other --actor mallory")
    assert p.returncode != 0, "a second init as someone else on the same machine was accepted"
    assert ident.read_text() == held, "a second init overwrote this machine's declared identity"
    assert "identity" in (p.stdout + p.stderr).lower(), f"the refusal does not say why: {p.stderr!r}"
