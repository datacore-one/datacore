"""Fixtures and the collection gate for the ledger-upgrade Phase 4 evals (O1-O4).

RED BY DESIGN until Phase 4 ships (PLAN.md, "Method: evals first"). An ordinary
`pytest` run -- CI's validate-pr, a pre-push -- does not collect them, so they
do not turn every unrelated change red while they wait. They are collected when
asked for, and by the promise scoreboard:

    LEDGER_UPGRADE_EVALS=1 pytest .datacore/lib/tests/ledger_upgrade -v
    PROMISE_EVALS_ALL=1    ...   (the scoreboard's switch; see promise_gate.py)

When a row turns green it is promoted into the ordinary run (the gate below is
narrowed), so a regression then fails CI. The index, with each row's status
and seeded failure, is `<system space>/1-tracks/dev/ledger-upgrade/EVALS.yaml`.
This mirrors promise_gate.py for evals that are not promise evals; it should
move to the protected `.datacore/evals/` once PLAN step E3 creates it.

State isolation (DATACORE_STATE, git config, ledger keys) comes from
`.datacore/conftest.py`, which `.datacore/pytest.ini` makes apply here.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
LIB = HERE.parents[1]
for p in (str(LIB), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

_ASKED = os.environ.get("LEDGER_UPGRADE_EVALS") == "1" or os.environ.get("PROMISE_EVALS_ALL") == "1"
collect_ignore_glob = [] if _ASKED else ["test_*.py"]

import _ledger_drill  # noqa: E402


@pytest.fixture
def sandbox(tmp_path_factory, monkeypatch):
    """A sandbox copy of a real Phase-1 space, whose baseline cycle is clean.

    Fails (never skips) when the live space changed during the eval: that would
    mean the eval wrote where it must only read.
    """
    d = _ledger_drill
    live = d.live_space()
    before = d.source_digest(live)
    root = Path(os.path.realpath(tmp_path_factory.mktemp("ledger-drill")))
    space = d.copy_space(live, root)
    monkeypatch.setenv("DATACORE_ACTOR", d.HUMAN)
    monkeypatch.setenv("DATACORE_ROOT", str(root))
    baseline = d.cycle(space)
    if baseline.stopped:
        pytest.fail(f"SETUP: the unmodified sandbox copy does not cycle cleanly ({baseline.reason}); "
                    "this is not a gesture result")
    yield space
    after = d.source_digest(live)
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert not changed, f"LIVE SPACE CHANGED during the eval: {changed}"
