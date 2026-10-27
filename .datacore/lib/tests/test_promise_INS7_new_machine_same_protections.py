"""INS-7: "A machine added to an install gets the same protections as the
existing ones, including safety hooks, update guard and schedules."

Kind: deterministic (the add-a-machine installer, lib/agent_host_setup.sh,
installs every protection) plus production contract (every machine in the
fleet has them today).

Protections:
  - safety hooks: every git checkout of a space (and the root) runs Datacore's
    pre-commit -- via core.hooksPath -> .datacore/githooks, or a
    .git/hooks/pre-commit link (install_space_hooks.py);
  - update guard: /etc/needrestart/conf.d/datacore.conf (OPS-5's file);
  - schedules: the job verifier is scheduled on the machine.

Seeded failure: a host installer that schedules jobs but never installs the
needrestart guard or the hooks (OI-11: the conf is referenced by no installer).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

import fleet_reach

LIB = Path(__file__).resolve().parents[1]
INSTALLER = LIB / "agent_host_setup.sh"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]

PROTECTIONS = {
    "safety hooks": r"core\.hooksPath|install_space_hooks|githooks|hooks/pre-commit",
    "update guard": r"needrestart",
    "schedules": r"job_verify",
}


@pytest.mark.parametrize("protection", sorted(PROTECTIONS))
def test_the_add_a_machine_installer_installs(protection):
    code = "\n".join(l for l in INSTALLER.read_text().splitlines() if not l.lstrip().startswith("#"))
    assert re.search(PROTECTIONS[protection], code), (
        f"{INSTALLER.name} sets up a new machine without its {protection}")


_PROBE = r'''
for d in ~/Data ~/Data/[0-9]-*; do
  [ -e "$d/.git" ] || continue
  hp=$(git -C "$d" config core.hooksPath)
  if [ -n "$hp" ] && [ -x "$hp/pre-commit" ]; then s=ok; elif [ -e "$d/.git/hooks/pre-commit" ]; then s=ok; else s=NONE; fi
  echo "HOOK $(basename "$d") $s"
done
[ -f /etc/needrestart/conf.d/datacore.conf ] && echo "GUARD ok" || echo "GUARD NONE"
(crontab -l 2>/dev/null; systemctl list-timers --all --no-legend 2>/dev/null; systemctl --user list-timers --all --no-legend 2>/dev/null) | grep -q -e job_verify -e job-verify && echo "SCHED ok" || echo "SCHED NONE"
'''


@pytest.mark.production
@pytest.mark.parametrize("host", ["box", "nightshift", "hermes", "plur-claw"])
def test_every_machine_has_every_protection(host):
    # Where the machine works (roster): its host, its k3s pod, or both. A pod's
    # schedule is its supercronic file; the OS update guard belongs to the node
    # (a container image is never updated in place), checked on the host.
    for pl in fleet_reach.places(host):
        probe = _PROBE if pl.kind != "pod" else (
            _PROBE.split("[ -f /etc/needrestart")[0] + 'echo "GUARD node"\n'
            + fleet_reach.schedule_listing(pl) + ' | grep -q -e job_verify -e job-verify && echo "SCHED ok" || echo "SCHED NONE"\n')
        try:
            out = fleet_reach.run_at(pl, probe, timeout=45)
        except subprocess.TimeoutExpired:
            pytest.fail(f"{host} ({pl.kind}): could not look (timeout) -- could not tell is not a pass")
        lines = out.stdout.splitlines()
        assert any(l.startswith("GUARD") for l in lines), f"{host} ({pl.kind}): could not look: {out.stderr[-200:]}"
        missing = [l for l in lines if l.endswith("NONE")]
        assert missing == [], f"{host} ({pl.kind}) lacks protections the others have: {missing}"
