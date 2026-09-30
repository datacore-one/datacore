"""OPS-14: A machine in the fleet that has gone quiet for more than 6 hours is reported
to the owner, and the list of machines comes from the roster, not from a script.

Kind: deterministic. The REAL fleet monitor (every manifest job named `*fleet-probe*`;
today `box-fleet-probe`, cos_fleet_probe.sh, deployed from chief-of-staff's server/lib
the way deploy.sh ships it) runs against a throwaway install whose roster
(`$DATACORE_ROOT/.datacore/registry/infrastructure.yaml`, the file every roster reader
resolves, jobs/manifest.roster_path) names made-up machines. Nothing reaches the
network: the commands a probe can use to reach a machine (`timeout`, `ssh`, `nc`,
`ping`) are stubs that answer from the sandbox's own table of which machine is up,
and record every target they were asked about. `sleep` returns at once and `date`
reads a simulated clock, so an eight-hour timeline of 15-minute runs takes seconds.

What "quiet" is measured by (from existing code, not invented here): the fleet probe's
own answer -- a machine is quiet from the last run at which it answered (`UP` in
fleet-probe.log, the job's declared artifact) until the next. That is the only
fleet-wide liveness signal that exists; the ledger's presence detector says in so many
words that an actor that stops writing is NOT an error, and heartbeats exist only on
some hosts. "Reported to the owner" is a message through cos_alert.sh, the box's alert
path to The Firm group (stubbed here to record, never to send).

Graded:
- the monitor asks about exactly the always-on machines in the roster -- every one, and
  nothing that is not in it (a hard-coded address is a target the roster does not name);
- over a simulated timeline where one server stops answering, the owner gets a report
  naming it no later than 6 hours after its last answer, and no report names a machine
  that kept answering;
- with every machine answering, no machine is reported.
- (static) the monitor's code holds no machine address or name=address pair: the list
  cannot drift from the roster.

Seeded failure (fleet week simulator, 2026-09-30, fault F6): a host offline for a day
was noticed by nothing. The only designed detector, cos_fleet_probe.sh, hard-codes host
names and addresses instead of reading the roster, so it cannot follow a changed roster
(or the sandbox), and after the host came back no check on it went red either.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
DC = LIB.parent
COS_LIB = DC / "modules" / "chief-of-staff" / "server" / "lib"
MANIFEST = LIB / "jobs" / "manifest.yaml"
DEPLOYED = ("cos_*", "winston_*", "miles_delivery.*", "lens_analyze.py", "ws_chat_probe.py")
QUIET_LIMIT = dt.timedelta(hours=6)
STEP = dt.timedelta(minutes=15)

#: The sandbox fleet. Every identifier is unique and under .invalid, so a stub can tell
#: which machine a target means, and nothing real can ever resolve.
FLEET = {
    "alpha": {"kind": "server"},
    "beta": {"kind": "server"},
    "gamma": {"kind": "server"},
    "desk": {"kind": "workstation"},
}


def _ids(name: str) -> tuple[str, ...]:
    return (f"{name}.sim.invalid", f"sim-{name}", name)


def _roster() -> dict:
    return {"roles": {"always_on": "alpha", "console": "desk"},
            "servers": {n: {"kind": c["kind"], "ssh_alias": f"sim-{n}", "ledger_actors": [f"{n}-actor"],
                            "access": {"hostname": f"{n}.sim.invalid", "actor": f"{n}-actor",
                                       "data_root": "~/Data"}}
                        for n, c in FLEET.items()}}


# ----------------------------------------------------------------------------- stubs

_NET_STUB = r'''#!{py}
"""Reachability stand-in: answers from the sandbox table, records every target."""
import json, os, re, sys
from pathlib import Path
S = Path(os.environ["SIM_NET"])
tool = Path(sys.argv[0]).name
args = sys.argv[1:]
text = " ".join(args)
table = json.loads((S / "up.json").read_text())
ids = json.loads((S / "ids.json").read_text())
now = (S / "now").read_text().strip()
m = re.search(r"/dev/tcp/([^/\s\"']+)/", text)
cands = [m.group(1)] if m else [a for a in args if not a.startswith("-")]
if tool == "timeout" and not m:
    # timeout DURATION CMD...: not a reachability test; run the command
    rest = args[1:] if args and not args[0].startswith("-") else args[2:]
    os.execvp(rest[0], rest)
if tool == "ssh":
    cands = [a for i, a in enumerate(args) if not a.startswith("-")
             and not (i and args[i - 1] in ("-o", "-p", "-i", "-l", "-F", "-J"))][:1]
if tool == "nc":
    cands = [a for a in args if not a.startswith("-") and not a.isdigit()][:1]
if tool == "ping":
    cands = [a for a in args if not a.startswith("-") and not re.fullmatch(r"[\d.]+", a)][-1:] or args[-1:]
host = None
for c in cands:
    for name, names in ids.items():
        if c in names or any(c.startswith(n + ":") or c.endswith("@" + n) for n in names):
            host = name
with open(S / "targets.jsonl", "a") as f:
    f.write(json.dumps({"at": now, "tool": tool, "target": cands[:1], "host": host}) + "\n")
sys.exit(0 if host and table.get(host) else 1)
'''

_DATE_STUB = r'''#!{py}
"""`date` on the simulated clock (UTC). Supports -u, -d @EPOCH, +FORMAT."""
import os, sys, datetime as dt
from pathlib import Path
now = dt.datetime.fromisoformat((Path(os.environ["SIM_NET"]) / "now").read_text().strip())
fmt = "%a %b %d %H:%M:%S UTC %Y"
args = sys.argv[1:]
i = 0
while i < len(args):
    a = args[i]
    if a in ("-u", "--utc"):
        pass
    elif a == "-d" and i + 1 < len(args) and args[i + 1].startswith("@"):
        now = dt.datetime.fromtimestamp(int(args[i + 1][1:]), dt.timezone.utc); i += 1
    elif a.startswith("+"):
        fmt = a[1:].replace("%F", "%Y-%m-%d").replace("%T", "%H:%M:%S")
        fmt = fmt.replace("%s", str(int(now.replace(tzinfo=dt.timezone.utc).timestamp())))
    i += 1
print(now.strftime(fmt))
'''


def _sandbox(tmp: Path) -> dict:
    home = tmp / "home"
    data = home / "Data"
    lib = data / ".datacore" / "lib"
    lib.mkdir(parents=True)
    for entry in LIB.iterdir():
        if entry.name not in ("tests", "__pycache__"):
            (lib / entry.name).symlink_to(entry)
    for pat in DEPLOYED:
        for f in COS_LIB.glob(pat):
            if f.is_file():
                (lib / f.name).unlink(missing_ok=True)
                shutil.copy2(f, lib / f.name)
    rec = home / "recorded"
    rec.mkdir()
    alert = lib / "cos_alert.sh"
    alert.unlink(missing_ok=True)
    alert.write_text(f"#!/bin/sh\nprintf '%s\\t%s\\n' \"$(cat {tmp}/net/now)\" \"$*\" >> {rec}/alerts.tsv\n")
    alert.chmod(0o755)
    reg = data / ".datacore" / "registry"
    reg.mkdir(parents=True)
    (reg / "infrastructure.yaml").write_text(yaml.safe_dump(_roster(), sort_keys=False))
    net = tmp / "net"
    net.mkdir()
    (net / "ids.json").write_text(json.dumps({n: list(_ids(n)) for n in FLEET}))
    bin_dir = home / "bin"
    bin_dir.mkdir()
    for tool in ("timeout", "ssh", "nc", "ping"):
        (bin_dir / tool).write_text(_NET_STUB.replace("{py}", sys.executable))
        (bin_dir / tool).chmod(0o755)
    (bin_dir / "date").write_text(_DATE_STUB.replace("{py}", sys.executable))
    (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n")
    # the host's python3 (the box's has PyYAML, as every roster reader needs)
    (bin_dir / "python3").symlink_to(sys.executable)
    for t in ("date", "sleep"):
        (bin_dir / t).chmod(0o755)
    (home / ".datacore" / "cos").mkdir(parents=True)
    env = {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8",
           "DATACORE_ROOT": str(data), "DATACORE_HOME": str(data), "SIM_NET": str(net),
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    return {"home": home, "lib": lib, "net": net, "rec": rec, "env": env}


def _monitor_jobs() -> list[dict]:
    jobs = [j for j in yaml.safe_load(MANIFEST.read_text())["jobs"] if "fleet-probe" in j["name"]]
    assert jobs, "the manifest declares no fleet monitor (a job named *fleet-probe*)"
    return jobs


def _script(job: dict) -> Path:
    m = re.search(r"~/Data/\.datacore/lib/(\S+?\.(?:sh|py))", job["cmd"])
    assert m, f"cannot find the script in {job['name']}'s command: {job['cmd']}"
    name = m.group(1)
    return COS_LIB / name if (COS_LIB / name).is_file() else LIB / name


def _timeline(sb: dict, job: dict, up_at, start: dt.datetime, hours: float) -> list[tuple[dt.datetime, str]]:
    """Run the monitor as its schedule does (every 15 minutes) over `hours` of
    simulated time. `up_at(host, t)` says whether a machine answers at t."""
    script = sb["lib"] / _script(job).name
    log = sb["home"] / ".datacore" / "cos" / "fleet-probe.log"
    t = start
    while t <= start + dt.timedelta(hours=hours):
        (sb["net"] / "now").write_text(t.isoformat())
        (sb["net"] / "up.json").write_text(json.dumps({h: bool(up_at(h, t)) for h in FLEET}))
        subprocess.run(["bash", "-c", f"{script} >> {log} 2>&1"], env=sb["env"], cwd=sb["home"],
                       capture_output=True, text=True, timeout=120)
        t += STEP
    alerts = []
    p = sb["rec"] / "alerts.tsv"
    for line in (p.read_text().splitlines() if p.exists() else []):
        at, _, msg = line.partition("\t")
        alerts.append((dt.datetime.fromisoformat(at), msg))
    return alerts


def _targets(sb: dict) -> list[dict]:
    p = sb["net"] / "targets.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def _names(msg: str) -> set[str]:
    return {h for h in FLEET if any(re.search(rf"(?<![\w-]){re.escape(i)}(?![\w-])", msg) for i in _ids(h))}


START = dt.datetime(2026, 9, 28, 0, 0)
SERVERS = sorted(n for n, c in FLEET.items() if c["kind"] != "workstation")


# ------------------------------------------------------------------------------ tests

@pytest.mark.parametrize("job", _monitor_jobs(), ids=lambda j: j["name"])
def test_the_monitor_asks_about_exactly_the_rostered_machines(tmp_path, job):
    sb = _sandbox(tmp_path)
    _timeline(sb, job, lambda h, t: True, START, 0.25)
    targets = _targets(sb)
    asked = {t["host"] for t in targets if t["host"]}
    # counted, never printed: a foreign target may be a real machine's address
    n_foreign = len({str(t["target"]) for t in targets if not t["host"]})
    assert n_foreign == 0, (f"{job['name']} probed {n_foreign} target(s) the roster does not name -- "
                         f"its machine list does not come from the roster")
    # the always-on host runs the monitor; asking about itself is allowed, not required
    missing = sorted(set(SERVERS) - asked - {_roster()["roles"]["always_on"]})
    assert not missing, f"{job['name']} never asked about rostered machines: {missing}"


@pytest.mark.parametrize("job", _monitor_jobs(), ids=lambda j: j["name"])
def test_a_server_quiet_for_more_than_6_hours_is_reported(tmp_path, job):
    sb = _sandbox(tmp_path)
    last_answer = START + dt.timedelta(hours=1)
    alerts = _timeline(sb, job, lambda h, t: h != "gamma" or t <= last_answer, START, 8)
    about_gamma = [(at, m) for at, m in alerts if "gamma" in _names(m)]
    in_time = [(at, m) for at, m in about_gamma if last_answer < at <= last_answer + QUIET_LIMIT]
    assert in_time, (f"gamma stopped answering at {last_answer:%H:%M} and by "
                     f"{last_answer + QUIET_LIMIT:%H:%M} the owner had no report naming it "
                     f"({len(alerts)} report(s) in all: {[m for _, m in alerts][:3]})")
    wrong = [m for _, m in alerts if _names(m) & {"alpha", "beta"}]
    assert not wrong, f"machines that kept answering were reported: {wrong[:3]}"


@pytest.mark.parametrize("job", _monitor_jobs(), ids=lambda j: j["name"])
def test_when_every_server_answers_nothing_is_reported(tmp_path, job):
    sb = _sandbox(tmp_path)
    alerts = _timeline(sb, job, lambda h, t: FLEET[h]["kind"] != "workstation", START, 8)
    named = [m for _, m in alerts if _names(m) & set(SERVERS)]
    assert not named, f"servers that answered all along were reported: {named[:3]}"
    assert _targets(sb), "the monitor never asked about any machine"


@pytest.mark.parametrize("job", _monitor_jobs(), ids=lambda j: j["name"])
def test_a_sleeping_workstation_is_not_reported(tmp_path, job):
    """Judgement call for the owner: the roster says a workstation sleeps (jobs/awake.py
    reads the same `kind`), so eight hours of a shut laptop is not a quiet machine."""
    sb = _sandbox(tmp_path)
    alerts = _timeline(sb, job, lambda h, t: h != "desk", START, 8)
    assert not [m for _, m in alerts if "desk" in _names(m)], "a sleeping workstation was reported"


_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_PAIR = re.compile(r"\b[a-z][\w-]*=[\w.-]+:\d{2,5}\b")


def test_no_fleet_monitor_holds_a_machine_list_of_its_own():
    real = LIB.parent / "registry" / "infrastructure.yaml"
    idents: set[str] = set()
    if real.is_file():
        for cfg in (yaml.safe_load(real.read_text()).get("servers") or {}).values():
            acc = (cfg or {}).get("access") or {}
            for v in (acc.get("hostname"), (cfg or {}).get("ssh_alias")):
                if v and v not in ("-",) and len(str(v)) > 3:
                    idents.add(str(v))
    problems = []
    for job in _monitor_jobs():
        path = _script(job)
        code = "\n".join(l for l in path.read_text().splitlines() if not l.strip().startswith("#"))
        if _IPV4.search(code):
            problems.append(f"{path.name}: a machine address is written into the code")
        if _PAIR.search(code):
            problems.append(f"{path.name}: a name=address:port target list is written into the code")
        named = sorted(i for i in idents if re.search(rf"(?<![\w.-]){re.escape(i)}(?![\w.-])", code))
        if named:
            problems.append(f"{path.name}: names {len(named)} rostered machine(s) by hand")
    assert not problems, "the fleet monitor keeps its own machine list: " + "; ".join(problems)
