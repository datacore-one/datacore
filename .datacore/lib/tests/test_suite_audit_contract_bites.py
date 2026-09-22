"""mac-suite-audit must fail on a run that tested nothing.

2026-09-22, by hand under the wrong interpreter: "0 passed, 0 unexplained, 0
accepted, 0 error across 21 suite(s); 0 suite(s) not green, 21 collected
nothing" -- and both the contract regex and the script's exit code read it as
green. A suite that ran no test is not a green suite.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import suite_audit  # noqa: E402

MANIFEST = LIB / "jobs" / "manifest.yaml"
FIXTURE = LIB / "tests" / "fixtures" / "jobs" / "mac-suite-audit.0.txt"


def _pattern() -> str:
    jobs = yaml.safe_load(MANIFEST.read_text())["jobs"]
    art = next(j for j in jobs if j["name"] == "mac-suite-audit")["artifacts"][0]
    assert art["check"] == "last_line_regex"
    return art["arg"]


def test_the_real_green_line_passes():
    assert re.search(_pattern(), FIXTURE.read_text().rstrip().splitlines()[-1])


@pytest.mark.parametrize("last_line", [
    "0 passed, 0 unexplained, 0 accepted, 0 error across 21 suite(s); 0 suite(s) not green, 21 collected nothing",
    "8648 passed, 9 unexplained, 0 accepted, 0 error across 21 suite(s); 3 suite(s) not green, 0 collected nothing",
    "8648 passed, 0 unexplained, 0 accepted, 1 error across 21 suite(s); 0 suite(s) not green, 0 collected nothing",
    "",
])
def test_anything_else_fails(last_line):
    assert not re.search(_pattern(), last_line)


def test_the_exit_code_agrees_with_the_contract():
    assert suite_audit.verdict([], []) == 0
    assert suite_audit.verdict(["lib/tests"], []) == 1
    assert suite_audit.verdict([], ["modules/x/tests"]) == 1, "a suite that collected nothing is not green"
