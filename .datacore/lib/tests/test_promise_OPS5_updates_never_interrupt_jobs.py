"""OPS-5: "Operating-system updates never interrupt a scheduled job that is running."

Kind: deterministic (every timer-driven service shipped in this repo is excluded
from needrestart's post-upgrade restarts by config/host/needrestart-datacore.conf)
plus production contract (every host carries that exact file, and every
timer-driven unit installed there -- our units, in /etc/systemd/system -- is
covered by it).

Seeded failure: a new scheduled unit whose name no override pattern matches
(e.g. `datacore-reindex.service`), or a host whose conf.d copy is missing or
differs from the repo copy -- unattended-upgrades then restarts the job mid-run
(box, 2026-09-25 06:10).
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

DC = Path(__file__).resolve().parents[2]
CONF = DC / "config" / "host" / "needrestart-datacore.conf"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
HOSTS = ["winston", "nightshift", "hermes", "plur-claw"]
VENDOR = {"droplet-agent-update.service"}  # the cloud provider's own agent, not a Datacore job


def _patterns(text: str) -> list[re.Pattern]:
    pats = []
    for m in re.finditer(r"\$nrconf\{override_rc\}\{qr\((.+?)\)\}\s*=\s*0\s*;", text):
        pats.append(re.compile(m.group(1)))
    return pats


def _protected(unit: str, pats) -> bool:
    return any(p.search(unit) for p in pats)


def test_parser_reads_the_conf():
    assert len(_patterns(CONF.read_text())) >= 5


def test_jobs_are_held_and_daemons_still_restart():
    pats = _patterns(CONF.read_text())
    assert _protected("nightshift-overnight.service", pats)
    assert _protected("datacore-fleet-sync.service", pats)
    assert not _protected("datacored.service", pats), "long-running daemons still restart"


def test_every_scheduled_unit_in_the_repo_is_protected():
    pats = _patterns(CONF.read_text())
    missing = []
    for timer in DC.rglob("*.timer"):
        if "node_modules" in timer.parts or "state" in timer.relative_to(DC).parts[:1]:
            continue
        m = re.search(r"^\s*Unit\s*=\s*(\S+)", timer.read_text(errors="replace"), re.M)
        unit = m.group(1) if m else timer.stem + ".service"
        if not _protected(unit, pats):
            missing.append(f"{unit} ({timer.relative_to(DC)})")
    assert missing == [], "scheduled units needrestart would restart mid-run:\n" + "\n".join(sorted(set(missing)))


_LIVE = ("sha256sum /etc/needrestart/conf.d/datacore.conf 2>&1; echo ---; "
         "for u in $(systemctl list-timers --all --no-legend --plain 2>/dev/null | awk '{print $NF}'); do "
         "echo \"$u $(systemctl show -p FragmentPath --value $u 2>/dev/null)\"; done")


@pytest.mark.production
@pytest.mark.parametrize("host", HOSTS)
def test_every_host_protects_every_installed_scheduled_job(host):
    want = hashlib.sha256(CONF.read_bytes()).hexdigest()
    try:
        out = subprocess.run(SSH + [host, _LIVE], capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        pytest.fail(f"{host}: could not look (timeout) -- could not tell is not a pass")
    assert out.returncode == 0 and "---" in out.stdout, f"{host}: could not look: {out.stderr[-200:]}"
    head, units = out.stdout.split("---", 1)
    assert head.split()[:1] == [want], f"{host}: conf.d/datacore.conf missing or differs from the repo: {head.strip()}"
    pats = _patterns(CONF.read_text())
    ours = [l.split()[0] for l in units.strip().splitlines()
            if len(l.split()) == 2 and l.split()[1].startswith("/etc/systemd/system/")]
    exposed = [u for u in ours if u not in VENDOR and not _protected(u, pats)]
    assert exposed == [], f"{host}: scheduled jobs an OS update would restart mid-run: {exposed}"
