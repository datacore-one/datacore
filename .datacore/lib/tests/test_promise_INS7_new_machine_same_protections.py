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
@pytest.mark.parametrize("host", ["winston", "nightshift", "hermes", "plur-claw"])
def test_every_machine_has_every_protection(host):
    try:
        out = subprocess.run(SSH + [host, _PROBE], capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        pytest.fail(f"{host}: could not look (timeout) -- could not tell is not a pass")
    lines = out.stdout.splitlines()
    assert any(l.startswith("GUARD") for l in lines), f"{host}: could not look: {out.stderr[-200:]}"
    missing = [l for l in lines if l.endswith("NONE")]
    assert missing == [], f"{host} lacks protections the others have: {missing}"
