"""The drill contracts pin a scenario COUNT, so the count has to stay true.

`delegation drill: 27 scenario(s), every control held` is asserted by the
mac-delegation-drill contract, and `15 scenario` by mac-drills. Those numbers
exist because "every control held" is equally true of a run that held three
controls -- a contract matching `\\d+` passes a drill whose scenarios quietly
stopped being collected, which is the same shape as gating on "all tests pass"
while the tests disappear.

Pinning moves the failure to the wrong place on its own, though: add a scenario
and the contract fails at 05:30 the next morning, in a telegram, about a change
made the previous afternoon. This asserts the same thing at PR time, and says
which file to edit.

Add a scenario, update the manifest. That is the whole contract.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

MANIFEST = LIB / "jobs" / "manifest.yaml"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_drill_{name}", LIB / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _job(name: str) -> dict:
    data = yaml.safe_load(MANIFEST.read_text())
    for job in data.get("jobs", []):
        if job.get("name") == name:
            return job
    pytest.fail(f"no job named {name!r} in {MANIFEST}")


def _pinned_number(job: dict) -> int:
    """The one count the job's artifact regex requires."""
    arg = job["artifacts"][0]["arg"]
    nums = re.findall(r"(?<!\\)\b(\d+)\b", arg)
    assert nums, f"{job['name']} pins no scenario count; its regex accepts any run: {arg!r}"
    return int(nums[0])


def test_delegation_drill_scenario_count_matches_its_contract():
    drill = _load("delegation_drill")
    actual = len(drill.DelegationDrill.SCENARIOS)
    pinned = _pinned_number(_job("nightshift-delegation-drill"))
    assert actual == pinned, (
        f"delegation_drill has {actual} scenarios; nightshift-delegation-drill pins {pinned}. "
        f"Update the `arg:` regex in {MANIFEST.relative_to(LIB.parent.parent)}."
    )


def _scenarios(mod) -> int:
    """However the drill names its class, find the one SCENARIOS tuple."""
    found = [v.SCENARIOS for v in vars(mod).values()
             if isinstance(v, type) and isinstance(getattr(v, "SCENARIOS", None), tuple)]
    assert len(found) == 1, f"{mod.__name__} exposes {len(found)} SCENARIOS tuples, expected 1"
    return len(found[0])


def test_chaos_drill_scenario_count_matches_the_drills_contract():
    actual = _scenarios(_load("ledger_chaos_drill"))
    # mac-drills asserts "3/3 held"; the per-drill counts are checked inside
    # audit_trio_run.sh, which greps each drill's own output for "15 scenario".
    runner = (LIB / "audit_trio_run.sh").read_text()
    m = re.search(r"ledger_chaos_drill:(\d+) scenario", runner)
    assert m, "audit_trio_run.sh no longer checks the chaos drill's scenario count"
    assert actual == int(m.group(1)), (
        f"ledger_chaos_drill has {actual} scenarios; audit_trio_run.sh expects "
        f"{m.group(1)}. Update the `drills` case in lib/audit_trio_run.sh."
    )
