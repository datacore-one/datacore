#!/usr/bin/env python3
"""The hourly phase-1 cycle runs at a different minute on every host.

Until 2026-10-18 every host ran `ledger_phase1_cycle.sh` at :25 -- the same
minute, every hour, all converging (and so pushing) into the same remotes.
The cycle converges with no actor, so it publishes in either push mode. The
owner's rule (2026-10-18): each host runs it at its own minute, and that
minute lies outside EVERY host's push window in `config/push_slots.yaml`, so
a cycle never races a slot job either.

Read from the schedule definitions this repository owns: the satellite cron
lines in `agent_host_setup.sh` and the box/nightshift contracts in
`jobs/manifest.yaml`. The box's crontab itself lives only on the box.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import ledger_transport  # noqa: E402

SETUP = LIB / "agent_host_setup.sh"
MANIFEST = LIB / "jobs" / "manifest.yaml"
# agent_host_setup.sh profile -> the host it installs (openclaw is plur-claw).
PROFILE_HOST = {"nightshift": "nightshift", "openclaw": "plur-claw", "hermes": "hermes"}


def _setup_minutes() -> dict[str, int]:
    """host -> minute of the phase-1 cycle cron line in each setup profile."""
    text = SETUP.read_text(encoding="utf-8")
    out: dict[str, int] = {}
    profile = None
    for line in text.splitlines():
        m = re.match(r"^\s{2}(\w[\w-]*)\)\s*$", line)
        if m:
            profile = m.group(1)
            continue
        m = re.search(r'CRON_LINES\+=\("(\d+) \* \* \* \* .*ledger_phase1_cycle\.sh', line)
        if m and profile in PROFILE_HOST:
            out[PROFILE_HOST[profile]] = int(m.group(1))
    return out


def _manifest_minutes() -> dict[str, int]:
    jobs = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["jobs"]
    out = {}
    for job in jobs:
        if job["name"] in ("box-phase1-cycle", "nightshift-phase1-cycle"):
            m = re.match(r"^cron (\d+) \* \* \* \*", job["schedule"])
            assert m, job["schedule"]
            out[job["machine"]] = int(m.group(1))
    return out


def _all_minutes() -> dict[str, int]:
    minutes = _setup_minutes()
    manifest = _manifest_minutes()
    assert set(minutes) == {"nightshift", "plur-claw", "hermes"}, minutes
    assert set(manifest) == {"box", "nightshift"}, manifest
    # The contract and the installer must agree on nightshift's minute.
    assert manifest["nightshift"] == minutes["nightshift"], (manifest, minutes)
    minutes["box"] = manifest["box"]
    return minutes


def test_every_host_runs_the_cycle_at_its_own_minute():
    minutes = _all_minutes()
    assert len(set(minutes.values())) == len(minutes), minutes


def test_no_cycle_minute_falls_inside_any_push_window():
    slots = ledger_transport.load_slots()
    width = slots["window_minutes"]
    for host, minute in _all_minutes().items():
        for slot_host, starts in slots["hosts"].items():
            for start in starts or []:
                assert (minute - start) % 60 >= width, (
                    f"{host}'s cycle at :{minute:02d} lies in {slot_host}'s "
                    f"push window :{start:02d}+{width}")
