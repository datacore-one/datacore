"""Fixtures and the collection gate for promise TSK-11's evals (ledger upgrade O1-O4).

"An ordinary edit to a task file never stops a space." The evals are promise
evals: promise_gate.py collects them in an ordinary run only once TSK-11 is
green in the committed baseline; the scoreboard (PROMISE_EVALS_ALL=1) always
collects them. LEDGER_UPGRADE_EVALS=1 collects them too:

    LEDGER_UPGRADE_EVALS=1 pytest .datacore/evals/ledger-upgrade/phase4 -v

The index, with each row's status and seeded failure, is
`<system space>/1-tracks/dev/ledger-upgrade/EVALS.yaml`.

State isolation (DATACORE_STATE, git config, ledger keys) comes from
`.datacore/conftest.py`, which `.datacore/pytest.ini` makes apply here.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
LIB = HERE.parents[2] / "lib"
for p in (str(LIB), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)



def pytest_ignore_collect(collection_path, config):
    if os.environ.get("LEDGER_UPGRADE_EVALS") == "1":
        return None
    import promise_gate
    return True if promise_gate.should_ignore(Path(str(collection_path))) else None


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
