"""Ten failing checks are not a pass.

The version check's job contract read its log for `0 FAIL`. The summary line
is "N check(s): X ok, F FAIL, ...", so `10 FAIL`, `20 FAIL` and `100 FAIL`
matched as well: a run with exactly ten failures read as healthy (found by the
second reviewer, 2026-11-22).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parents[1]


def _contract() -> str:
    jobs = yaml.safe_load((LIB / "jobs" / "manifest.yaml").read_text())["jobs"]
    job = next(j for j in jobs if j["name"] == "bridge-v2-verify")
    art = next(a for a in job["artifacts"] if a["path"].endswith("v2-verify.log"))
    assert art["check"] == "regex"
    return art["arg"]


def _log(failed: int) -> str:
    return (f"=== 2026-11-22 06:00:00 UTC -- v2 verify on box ===\n\n"
            f"  42 check(s): {42 - failed} ok, {failed} FAIL, 0 could-not-run\nv2-verify: exit {int(failed > 0)}\n")


def test_a_run_with_no_failures_passes():
    assert re.search(_contract(), _log(0), re.MULTILINE)


def test_ten_twenty_or_a_hundred_failures_do_not_pass():
    for failed in (1, 10, 20, 30, 40):
        assert not re.search(_contract(), _log(failed), re.MULTILINE), f"{failed} FAIL read as a pass"
