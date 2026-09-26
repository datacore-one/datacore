"""MEM-52: Agents don't invent work for themselves on a timer. They act only on named
duties or on requests.

Kind: production contract (read-only: local crontab, read-only ssh) against the jobs
manifest (.datacore/lib/jobs/manifest.yaml, the list of named duties).
For every machine (mac, box/winston, nightshift, hermes, plur-claw): every scheduled entry
(crontab lines, systemd timers' ExecStart) whose script launches a model -- `claude -p`,
nightshift's _run_claude, `hermes chat`, `openclaw agent`, `codex exec`, an Anthropic
messages call -- must be a named duty: a manifest job for that machine whose cmd runs that
script. A timer that starts a model and is not a declared duty is an agent finding its
own work (the pre-2026-07-29 Tris/Data heartbeats: "autonomous work" every 30 min).
The check itself is proven on fixtures: an undeclared model-launching heartbeat is
flagged, a declared duty and a model-free heartbeat are not.

Seeded failure: the fixture heartbeat that calls `claude -p "find useful work"` with no
manifest entry -> flagged (test_the_check_flags_an_undeclared_model_timer); verified.
"""
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / ".datacore" / "lib" / "jobs" / "manifest.yaml"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
HOSTS = {"box": "winston", "nightshift": "nightshift", "hermes": "hermes", "plur-claw": "plur-claw"}
MODEL = r"(\bclaude\b[^\n#]*(-p\b|--print)|_run_claude|hermes chat|openclaw agent|codex exec|messages\.create)"

# Prints "<script>\t<1 if it launches a model>" for every scheduled script on the machine.
PROBE = (
    "for f in $( (crontab -l 2>/dev/null; sudo -n crontab -l 2>/dev/null; "
    "for u in $(systemctl list-timers --all --no-legend --plain 2>/dev/null | awk '{print $NF}'); do "
    "systemctl cat \"$u\" 2>/dev/null | grep '^ExecStart'; "
    "s=${u%.timer}.service; systemctl cat \"$s\" 2>/dev/null | grep '^ExecStart'; done) "
    "| grep -v '^#' | grep -oE '/[^ ;\"]+\\.(sh|py)' | sort -u); do "
    "[ -f \"$f\" ] || continue; if grep -vE '^\\s*#' \"$f\" | grep -qE '" + MODEL.replace("'", "") + "'; "
    "then echo \"$f\t1\"; else echo \"$f\t0\"; fi; done; true"
)


def undeclared(scripts: dict[str, bool], jobs: list[dict], machine: str) -> list[str]:
    cmds = [j.get("cmd", "") for j in jobs if j.get("machine") == machine]
    return sorted(s for s, llm in scripts.items()
                  if llm and not any(Path(s).name in c for c in cmds))


def _parse(out: str) -> dict[str, bool]:
    rows = {}
    for line in out.splitlines():
        if "\t" in line:
            path, flag = line.rsplit("\t", 1)
            rows[path] = flag.strip() == "1"
    return rows


def test_the_check_flags_an_undeclared_model_timer():
    jobs = [{"machine": "hermes", "cmd": "~/Data/.datacore/lib/briefing_run.sh"}]
    scripts = {"/srv/agent/tris-heartbeat.sh": True, "/srv/agent/Data/.datacore/lib/briefing_run.sh": True,
               "/srv/agent/data-heartbeat.sh": False}
    assert undeclared(scripts, jobs, "hermes") == ["/srv/agent/tris-heartbeat.sh"]
    assert re.search(MODEL, 'claude -p "find useful work and do it"')
    assert not re.search(MODEL, "python3 org_query.py --assignee data")


def _jobs():
    return yaml.safe_load(MANIFEST.read_text())["jobs"]


@pytest.mark.production
def test_the_mac_starts_models_only_for_named_duties():
    r = subprocess.run(["bash", "-c", PROBE], capture_output=True, text=True, timeout=60)
    bad = undeclared(_parse(r.stdout), _jobs(), "mac")
    assert not bad, f"mac: scheduled model runs that are no named duty: {bad}"


@pytest.mark.production
@pytest.mark.parametrize("machine", list(HOSTS))
def test_each_agent_host_starts_models_only_for_named_duties(machine):
    r = subprocess.run([*SSH, HOSTS[machine], PROBE], capture_output=True, text=True, timeout=55)
    assert r.returncode == 0, f"{machine}: could not read its schedules ({r.stderr.strip()[-160:]})"
    bad = undeclared(_parse(r.stdout), _jobs(), machine)
    assert not bad, f"{machine}: scheduled model runs that are no named duty: {bad}"
