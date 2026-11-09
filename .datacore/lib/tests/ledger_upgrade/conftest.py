"""Collection gate for the ledger-upgrade evals kept in this folder.

RED BY DESIGN until their phase ships (2-datacore/1-tracks/dev/ledger-upgrade/
PLAN.md, "Method: evals first"). An ordinary `pytest` run -- CI's validate-pr,
a pre-push -- does not collect them; they are collected when asked for:

    LEDGER_UPGRADE_EVALS=1 pytest .datacore/lib/tests/ledger_upgrade -v
    PROMISE_EVALS_ALL=1    ...   (the scoreboard's switch; see promise_gate.py)

The Phase 4 evals (O1-O4) moved to .datacore/evals/ledger-upgrade/phase4/ on
2026-10-04 (step E3); this folder keeps the gate the other phases' evals use.
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

_ASKED = os.environ.get("LEDGER_UPGRADE_EVALS") == "1" or os.environ.get("PROMISE_EVALS_ALL") == "1"
# Green rows are promoted into every run (so a regression fails CI); the rest
# stay out of ordinary runs until their row turns green. Name a file here only
# once its EVALS.yaml row is green.
PROMOTED = {
    "test_p0v_void_is_the_only_cancel.py",                       # P0-V
    "test_t1_hand_written_line_refused_at_commit_and_push.py",   # T1
    "test_t3_future_hlc_refused_flagged_not_followed.py",        # T3
}
collect_ignore_glob = [] if _ASKED else sorted(
    p.name for p in HERE.glob("test_*.py") if p.name not in PROMOTED)
