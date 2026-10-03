"""Collection gate for the ledger-upgrade Phase 2 evals (V1-V5).

RED BY DESIGN until Phase 2 ships (PLAN.md, "Method: evals first"). An ordinary
`pytest` run -- CI, a pre-push -- does not collect an eval until it is green and
listed in PROMOTED below, so a red-by-design eval does not turn every unrelated
change red while it waits. They are collected when asked for, and by the
promise scoreboard:

    LEDGER_UPGRADE_EVALS=1 pytest .datacore/lib/tests/ledger_upgrade_verify -v
    PROMISE_EVALS_ALL=1    ...   (the scoreboard's switch; see promise_gate.py)

Same pattern as the Phase 4 folder (`../ledger_upgrade/conftest.py`). The index,
with each row's status and seeded failure, is
`<system space>/1-tracks/dev/ledger-upgrade/EVALS.yaml`. Implementers do not
edit these files (PLAN.md owner decision 5); a row is promoted by the owner or
with the owner's yes, once it is green.

State isolation (DATACORE_STATE, git config, ledger keys) comes from
`.datacore/conftest.py`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parents[1]
for p in (str(LIB), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

#: Evals that are green and guard against regression in ordinary runs.
PROMOTED: list[str] = []

_ASKED = os.environ.get("LEDGER_UPGRADE_EVALS") == "1" or os.environ.get("PROMISE_EVALS_ALL") == "1"
collect_ignore_glob = [] if _ASKED else [
    f.name for f in HERE.glob("test_*.py") if f.name not in PROMOTED]

pytest_plugins: list[str] = []

from _verify_fixtures import sandbox_root  # noqa: E402,F401  (fixture)
