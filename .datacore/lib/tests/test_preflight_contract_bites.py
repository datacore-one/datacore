"""`nightshift-preflight` must fail on the record a bad night would write.

A contract whose fixture passes proves the regex matches health. It says
nothing about whether it rejects sickness, and the record format lives in
another repository (nightshift `run.py: _record_preflight`), so the two can
drift apart silently. These are the lines that function writes.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

MANIFEST = Path(__file__).resolve().parents[1] / "jobs" / "manifest.yaml"


def _pattern() -> str:
    jobs = yaml.safe_load(MANIFEST.read_text())["jobs"]
    job = next(j for j in jobs if j["name"] == "nightshift-preflight")
    art = job["artifacts"][0]
    assert art["check"] == "last_line_regex"
    return art["arg"]


def test_a_clean_preflight_passes():
    assert re.search(_pattern(), "preflight: 0 space(s) would sit out, 0 finding(s) would refuse the run")


@pytest.mark.parametrize("last_line", [
    "preflight: 1 space(s) would sit out, 0 finding(s) would refuse the run",
    "preflight: 0 space(s) would sit out, 1 finding(s) would refuse the run",
    "preflight: 10 space(s) would sit out, 10 finding(s) would refuse the run",
    "preflight: deferred -- a run is active and has recorded its own",
    "",
])
def test_anything_else_fails(last_line):
    assert not re.search(_pattern(), last_line)
