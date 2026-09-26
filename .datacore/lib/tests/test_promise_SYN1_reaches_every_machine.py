"""SYN-1: A change made on any machine reaches every other machine within one
sync cycle.

Kind: deterministic + production contract.

Deterministic: three machines (tmp roots mac / box / nightshift, each with a
registry and a clone of one knowledge space, a local bare origin). A change is
made on one machine -- an edited note left uncommitted, and a ledger event --
then that machine runs one sync cycle (`ledger_transport.sync_all`, what the
hourly cycle and `sync` run) and every other machine runs its next one. Every
machine must then hold the change. Driven from each machine in turn.

Production (@production): "one sync cycle" only means something if every
machine runs one. Each host of the fleet (mac, box=winston, nightshift,
hermes, plur-claw) must have a scheduled space sync (phase-1 cycle, cos_sync,
fleet sync) that fires at least hourly. Read-only: `crontab -l`,
`systemctl list-timers`, `launchctl list`.

Seeded failure: the originating machine's cycle does not publish (converge
pulled and never pushed -- the pre-2026-08 one-way converge); the other
machines never see the change.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

import ledger_transport as lt
from ledger.log import EventLog, read_events

MACHINES = ("mac", "box", "nightshift")
SPACE = "9-fixture"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    import actor_identity
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    reg = tmp_path / "principals.yaml"
    reg.write_text("principals:\n" + "".join(
        f"  p-{m}:\n    kind: agent\n    writes_as: [{m}]\n" for m in MACHINES))
    monkeypatch.setattr(actor_identity, "PRINCIPALS", reg)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True, timeout=60)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True, timeout=60)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", str(hooks))):
        _git(seed, "config", k, v)
    (seed / "notes").mkdir()
    (seed / "notes" / "a.md").write_text("first\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "seed")
    assert _git(seed, "push", "-q", "origin", "HEAD:refs/heads/main").returncode == 0
    roots = {}
    for m in MACHINES:
        root = tmp_path / m / "Data"
        (root / ".datacore" / "registry").mkdir(parents=True)
        (root / ".datacore" / "registry" / "repositories.yaml").write_text(
            f"repositories:\n  {SPACE}:\n    category: knowledge\n")
        space = root / SPACE
        subprocess.run(["git", "clone", "-q", str(origin), str(space)], check=True, timeout=60)
        for k, v in (("user.email", f"{m}@t"), ("user.name", m), ("core.hooksPath", str(hooks))):
            _git(space, "config", k, v)
        roots[m] = root
    return roots


def _cycle(root: Path, monkeypatch) -> None:
    """One machine's cycle, run as that machine's declared writer."""
    monkeypatch.setenv("DATACORE_ACTOR", root.parent.name)
    lt.sync_all(root, quiet=True, include_code=False)


@pytest.mark.parametrize("origin_machine", MACHINES)
def test_a_change_reaches_every_other_machine_in_one_cycle(fleet, origin_machine, monkeypatch):
    src = fleet[origin_machine] / SPACE
    (src / "notes" / "a.md").write_text(f"edited on {origin_machine}\n")
    EventLog(src, origin_machine, sign=False).append(
        "item.create", {"id": f"from-{origin_machine}", "title": "t", "state": "NEXT"})

    _cycle(fleet[origin_machine], monkeypatch)
    for m in MACHINES:
        if m != origin_machine:
            _cycle(fleet[m], monkeypatch)

    for m in MACHINES:
        space = fleet[m] / SPACE
        assert (space / "notes" / "a.md").read_text() == f"edited on {origin_machine}\n", \
            f"{m} did not receive the note edited on {origin_machine}"
        ids = {(e.payload or {}).get("id") for e in read_events(space)}
        assert f"from-{origin_machine}" in ids, f"{m} did not receive the ledger event from {origin_machine}"


# ── production: every machine actually runs a cycle, at least hourly ───────

SYNC_WORDS = re.compile(r"phase1_cycle|cos_sync|git_fleet_sync|fleet-sync|ledger_transport|datacore_sync|/sync\b")
HOSTS = {"box": "winston", "nightshift": "nightshift", "hermes": "hermes", "plur-claw": "plur-claw"}


def _hourly_cron(lines: str) -> bool:
    for line in lines.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or not SYNC_WORDS.search(line):
            continue
        fields = line.split()
        if len(fields) >= 2 and fields[1] in ("*", "*/1"):
            return True
    return False


def _hourly_timer(lines: str) -> bool:
    """list-timers: NEXT ... LAST ... UNIT; hourly when next-last <= 1h."""
    from datetime import datetime
    for line in lines.splitlines():
        if not SYNC_WORDS.search(line):
            continue
        stamps = re.findall(r"\w{3} (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if len(stamps) >= 2:
            nxt, last = (datetime.fromisoformat(s) for s in stamps[:2])
            if 0 < (nxt - last).total_seconds() <= 3600 + 60:
                return True
    return False


def _ssh(host: str, cmd: str) -> str:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, cmd],
                       capture_output=True, text=True, timeout=40)
    if r.returncode not in (0, 1):
        pytest.fail(f"could not read {host}'s schedule (ssh rc={r.returncode}): {r.stderr.strip()[:120]}")
    return r.stdout


def _mac_join_agent() -> bool:
    """The mac is a visitor since 2026-09-22: io.datacore.join ticks and converges on join."""
    import plistlib
    loaded = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=20).stdout
    plist = Path.home() / "Library" / "LaunchAgents" / "io.datacore.join.plist"
    if "io.datacore.join" not in loaded or not plist.exists():
        return False
    interval = plistlib.loads(plist.read_bytes()).get("StartInterval") or 10**9
    return interval <= 3600


@pytest.mark.production
def test_every_machine_runs_a_space_sync_at_least_hourly():
    from concurrent.futures import ThreadPoolExecutor
    missing = []
    cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=20).stdout
    if not (_hourly_cron(cron) or _mac_join_agent()):
        missing.append("mac")
    cmd = ("crontab -l 2>/dev/null; echo ---; systemctl --user list-timers --all 2>/dev/null; "
           "systemctl list-timers --all 2>/dev/null")
    with ThreadPoolExecutor(len(HOSTS)) as pool:
        outs = dict(zip(HOSTS, pool.map(lambda h: _ssh(h, cmd), HOSTS.values())))
    for name, out in outs.items():
        cron_part, _, timers = out.partition("---")
        if not (_hourly_cron(cron_part) or _hourly_timer(timers)):
            missing.append(name)
    assert not missing, f"no space sync scheduled at least hourly on: {', '.join(missing)}"
