"""nightshift-venture-heartbeat must go red when a tick failed, and say why."""
import re
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]


def _pattern():
    jobs = yaml.safe_load((LIB / "jobs" / "manifest.yaml").read_text())["jobs"]
    return next(j for j in jobs if j["name"] == "nightshift-venture-heartbeat")["artifacts"][0]["arg"]


def test_a_clean_tick_passes():
    assert re.search(_pattern(), "[2026-09-22T20:37:17Z] 2 ventures active, 2 idle, 0 failed")


@pytest.mark.parametrize("line", [
    "[2026-09-22T20:26:16Z] 0 ventures active, 2 idle, 2 failed: datacore=sense_error(TypeError); meridian=usage_limit(usage limit; resets 9:50pm (UTC))",
    "[2026-09-22T20:26:16Z] 1 ventures active, 2 idle, 10 failed: x=error",
    "Venture heartbeat starting (interval: 1800s, ventures: all active)",
])
def test_a_failed_tick_or_no_tick_fails(line):
    assert not re.search(_pattern(), line)
