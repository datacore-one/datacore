#!/usr/bin/env python3
"""Run a week of this install's real schedule, in about an hour, and list what breaks.

Owner, 2026-09-30 (issue #222): instead of waiting seven real nights to learn
whether the fleet is stable, simulate the week. This builds a sandbox fleet --
one scratch checkout per machine in the install's roster, each with the real
job list -- steps a simulated clock through every job's schedule in order,
injects the faults the fleet actually met between 2026-09-26 and 09-30, and
after each simulated night runs the checks the fleet trusts: the ledger
verifier, job_verify, and the promise evals the sandbox can run.

THE CLOCK. libfaketime, preloaded into every process a job starts (python,
bash, git, date), so no product code changes to be simulated. macOS will not
preload into its protected binaries, so the run happens in a Linux container
(`sim/Dockerfile`), started with --network none: nothing in it can reach the
real fleet, GitHub or Telegram, whatever a job tries.

THE MODELS. Every model runtime on the PATH (`claude`, `hermes`, `codex`,
`openclaw`) is `sim/stand_in.py`: local, deterministic, free. The fault
schedule makes some of them misbehave -- stash, autostash, reset, hand-edit
another writer's log, say "done" without doing -- and those commands go
through the real tool-policy guard, the way a real runtime's would.

THE NETWORK. `sim/host_stubs.py` stands in for ssh, rsync, scp, gh, sudo,
systemctl and crontab. ssh to a roster host runs the command as that sandbox
machine; an offline host times out the way ssh does.

WHAT IT DOES NOT CHANGE. It never edits a promise eval, never weakens a check,
and never touches the real install: the seed is read from git (tracked files
at HEAD) plus a short list of the install's own gitignored configuration files
(roster -- addresses stripped --, principals, policy, space roster, local job
list). No credential is read: no .env, no secrets directory.

    fleet_week_sim.py docker [--days 7] [--out DIR] [--selftest]   # on the Mac
    fleet_week_sim.py compare RUN [--prev RUN]                     # new / fixed / still red
    fleet_week_sim.py prepare --seed DIR                           # seed only
    fleet_week_sim.py run --seed DIR --out DIR [--days N]          # inside Linux

Output: <out>/report.json and <out>/report.md -- every break, day by day, with
its machine, job, first failing check and a one-line cause guess, and for each
injected fault whether anything noticed it -- plus <out>/summary.json, the
small stable view `compare` reads. `docker` without --out writes to
~/.datacore/state/fleet-sim/<date>-<N>d/ and compares with the previous run
there (<out>/compare.md). The slash command is /fleet-sim.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import dataclasses
import datetime as dt
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

LIB = Path(__file__).resolve().parent
SIMDIR = LIB / "sim"
IMAGE = "datacore-fleet-sim"
UTC = dt.timezone.utc

#: What the harness itself is made of: overlaid onto the seed from the working
#: tree, so an uncommitted harness can run against the committed product.
HARNESS_FILES = (".datacore/lib/fleet_week_sim.py", ".datacore/lib/sim/stand_in.py",
                 ".datacore/lib/sim/host_stubs.py", ".datacore/lib/sim/Dockerfile",
                 ".datacore/lib/tests/test_fleet_week_sim.py")

#: The install's gitignored configuration a machine needs to behave like
#: itself. Nothing else untracked is ever read.
CONFIG_FILES = {
    "infrastructure.yaml": ".datacore/registry/infrastructure.yaml",
    "principals.yaml": ".datacore/registry/principals.yaml",
    "approvals_policy.local.yaml": ".datacore/config/approvals_policy.local.yaml",
    "install.yaml": "install.yaml",
    "manifest.local.yaml": ".datacore/lib/jobs/manifest.local.yaml",
}

#: Small tracked files that make a scratch space behave like the real one.
SPACE_MARKERS = (".gitignore", ".datacore/ledger-phase", ".datacore/ledger-edit-protocol",
                 ".datacore/ledger-org-header", ".datacore/structure-allow")

MODEL_RUNTIMES = ("claude", "hermes", "codex", "openclaw")
HOST_TOOLS = ("ssh", "rsync", "scp", "gh", "sudo", "systemctl", "crontab", "launchctl")

#: Jobs the sandbox cannot model, by the script they run, and why. They are
#: listed in the report as not simulated -- never counted as green.
NOT_MODELLED = {
    r"promise_nightly\.py": "the promise scoreboard: the harness runs the same evals itself after every "
                            "night (needs-gated), so it is not run a second time (about 10 min a night)",
}

#: Jobs modelled only when every machine can answer on :22 at its roster names
#: (Fleet._serve_ports: root in the container, and not a fleet nested in a job).
NEEDS_PORTS = {
    r"cos_fleet_probe\.sh": "probes each agent host's :22 at its roster name, and this sandbox could not "
                            "give its machines addresses (not root, or built inside another simulated "
                            "machine), so it could only ever say DOWN",
}

#: Listening sockets, by loopback address, for the machines that are online.
#: Module-level: a second fleet in the same process (the tests) reuses them.
_LISTENERS: dict = {}

#: Environment the sandbox adds to every job, each because a job WAITS in real
#: time for something the sandbox never delivers. Listed in the report.
SANDBOX_ENV = {
    # cos_oura_gate.py polls every 10 min until 07:00 UTC for the Mac's health
    # reading, then fails open. The fail-open path is what runs; the three
    # real hours of polling are skipped.
    "OURA_GATE_DEADLINE_UTC": "00:00",
}

VISITOR_AWAKE = (8, 23)      # a workstation's waking hours, UTC
JOIN_EVERY_H = 4             # visitor_join: every 4 waking hours
CHECK_AT = (9, 0)            # the morning check after each night


# ═════════════════════════════════════════════════════════════════════════════
# The schedule reader
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class Spec:
    kind: str                       # cron | times | interval | visitor | daemon | unknown
    text: str = ""
    cron: tuple = ()                # (minutes, hours, doms, months, dows, dom_star, dow_star)
    times: tuple = ()               # ((h, m), ...)
    dows: frozenset | None = None   # cron numbering, 0 = Sunday
    every_s: int = 0
    trigger: str = ""


_CRON = re.compile(r"(?<![\w:/])((?:[\d*][\d*/,-]*\s+){4}[\d*][\d*/,-]*)(?![\w:])")
_HHMM = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_EVERY = re.compile(r"(?:every|interval|~)\s*(\d+)\s*(seconds|second|secs|sec|s|minutes|minute|min|m|hours|hour|h)\b",
                    re.I)
_STARTINTERVAL = re.compile(r"StartInterval\s+(\d+)\s*s", re.I)
_DAYS = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5,
         "saturday": 6}


def _field(expr: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in expr.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = int(part)
            b = hi if step > 1 else a
        out.update(range(a, b + 1, step))
    return out


def parse_schedule(text: str, trigger: str | None = None, cmd: str = "") -> Spec:
    """A manifest `schedule` (cron, or the prose the manifest uses) as a Spec."""
    t = str(text or "")
    low = t.lower()
    if trigger in ("wake", "join", "arrival", "awake"):
        every = _interval(t)
        return Spec("visitor", t, trigger=trigger, every_s=every)
    if "keepalive" in low or "sidecar" in low or "--serve" in cmd or low.startswith("continuous"):
        return Spec("daemon", t)
    m = _CRON.search(t)
    if m:
        f = m.group(1).split()
        dows = {d % 7 for d in _field(f[4], 0, 7)}
        return Spec("cron", t, cron=(_field(f[0], 0, 59), _field(f[1], 0, 23), _field(f[2], 1, 31),
                                     _field(f[3], 1, 12), dows, f[2] == "*", f[4] == "*"))
    every = _interval(t)
    if every:
        return Spec("interval", t, every_s=every)
    times = tuple((int(h), int(mm)) for h, mm in _HHMM.findall(t))
    if times:
        dows = None
        if "weekday" in low:
            dows = frozenset({1, 2, 3, 4, 5})
        else:
            named = {n for d, n in _DAYS.items() if d in low}
            dows = frozenset(named) if named else None
        return Spec("times", t, times=times, dows=dows)
    if "continuous" in low or "daemon" in low:
        return Spec("daemon", t)
    return Spec("unknown", t)


def _interval(t: str) -> int:
    m = _STARTINTERVAL.search(t)
    if m:
        return int(m.group(1))
    m = _EVERY.search(t)
    if not m:
        return 0
    n, unit = int(m.group(1)), m.group(2).lower()
    return n * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1)


def _cron_day(spec: Spec, day: dt.date) -> bool:
    _mins, _hours, doms, months, dows, dom_star, dow_star = spec.cron
    if day.month not in months:
        return False
    dow = (day.weekday() + 1) % 7
    dom_ok, dow_ok = day.day in doms, dow in dows
    if dom_star or dow_star:
        return dom_ok and dow_ok
    return dom_ok or dow_ok


def fires(spec: Spec, day: dt.date, *, visitor: bool = False,
          min_interval_s: int = 3600) -> list[dt.datetime]:
    """Every instant `spec` fires on `day` (UTC), compressed to `min_interval_s`.

    Compression: a job that fires more often than the minimum interval fires
    once per interval instead, at its first instant in it -- a week of
    quarter-hourly jobs at full rate would not fit the wall-time budget, and
    every artifact the manifest checks allows at least an hour.

    A visitor (a workstation that sleeps) runs only while awake; a calendar
    job whose time fell while it slept runs once on wake, as launchd does.
    """
    base = dt.datetime(day.year, day.month, day.day, tzinfo=UTC)
    out: list[dt.datetime] = []
    if spec.kind == "cron":
        if _cron_day(spec, day):
            out = [base + dt.timedelta(hours=h, minutes=m)
                   for h in sorted(spec.cron[1]) for m in sorted(spec.cron[0])]
    elif spec.kind == "times":
        if spec.dows is None or (day.weekday() + 1) % 7 in spec.dows:
            out = sorted(base + dt.timedelta(hours=h, minutes=m) for h, m in spec.times)
    elif spec.kind == "interval":
        step = max(spec.every_s, 60)
        out = [base + dt.timedelta(seconds=s) for s in range(0, 86400, step)]
    elif spec.kind == "visitor":
        wake, sleep = VISITOR_AWAKE
        if spec.trigger in ("wake", "arrival"):
            return [base + dt.timedelta(hours=wake)]
        if spec.trigger == "join":
            # visitor_join runs these AFTER a converged join: one minute after it.
            return [base + dt.timedelta(hours=h, minutes=1) for h in range(wake, sleep, JOIN_EVERY_H)]
        step = max(spec.every_s or min_interval_s, min_interval_s)
        return [base + dt.timedelta(seconds=s)
                for s in range(wake * 3600, sleep * 3600, step)]
    else:
        return []
    out = _compress(out, min_interval_s)
    if visitor:
        wake, sleep = VISITOR_AWAKE
        awake = [t for t in out if wake <= t.hour < sleep]
        missed = len(awake) < len(out)
        out = awake
        if missed and base + dt.timedelta(hours=wake) not in out:
            out = [base + dt.timedelta(hours=wake)] + out
    return out


def _compress(times: list[dt.datetime], min_interval_s: int) -> list[dt.datetime]:
    kept: list[dt.datetime] = []
    for t in sorted(times):
        if not kept or (t - kept[-1]).total_seconds() >= min_interval_s:
            kept.append(t)
    return kept


# ═════════════════════════════════════════════════════════════════════════════
# The seed (host side): tracked code at HEAD plus the install's own config
# ═════════════════════════════════════════════════════════════════════════════

def _git_out(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          timeout=300).stdout


def _archive(repo: Path, dest: Path, *paths: str) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    arch = subprocess.run(["git", "-C", str(repo), "archive", "HEAD", *paths],
                          capture_output=True, timeout=600)
    if arch.returncode:
        raise RuntimeError(f"git archive {repo}: {arch.stderr.decode()[-300:]}")
    subprocess.run(["tar", "-x", "-C", str(dest)], input=arch.stdout, check=True, timeout=600)


def sanitize_roster(doc: dict) -> dict:
    """The roster without addresses, keys or notes: names, kinds, roles, actors."""
    servers = {}
    for name, cfg in (doc.get("servers") or {}).items():
        if not isinstance(cfg, dict):
            continue
        access = cfg.get("access") if isinstance(cfg.get("access"), dict) else {}
        keep = {"kind": cfg.get("kind") or "server",
                "ssh_alias": cfg.get("ssh_alias"),
                "ledger_actors": list(cfg.get("ledger_actors") or []),
                "access": {"actor": access.get("actor") or name, "hostname": name}}
        for k in ("manifest_machine", "setup_profile"):   # setup_profile: the host setup's kind
            if cfg.get(k):
                keep[k] = cfg[k]
        servers[str(name)] = keep
    return {"servers": servers, "roles": doc.get("roles") or {}}


def prepare(src: Path, seed: Path) -> Path:
    """Write the seed a sandbox fleet is built from. Reads git and CONFIG_FILES only."""
    import yaml
    sys.path.insert(0, str(src / ".datacore" / "lib"))
    if seed.exists():
        shutil.rmtree(seed)
    seed.mkdir(parents=True)
    _archive(src, seed / "core-src")
    for rel in HARNESS_FILES:
        p = src / rel
        if p.exists():
            (seed / "core-src" / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, seed / "core-src" / rel)
    for mod in sorted((src / ".datacore" / "modules").iterdir()):
        if (mod / ".git").exists():
            _archive(mod, seed / "modules" / mod.name)
    cfg = seed / "config"
    cfg.mkdir()
    for name, rel in CONFIG_FILES.items():
        p = src / rel
        if not p.exists():
            continue
        if name == "infrastructure.yaml":
            (cfg / name).write_text(yaml.safe_dump(sanitize_roster(yaml.safe_load(p.read_text()) or {}),
                                                   sort_keys=False))
        else:
            shutil.copy2(p, cfg / name)
    from spaces import space_for
    system = space_for("system", src)
    spaces = []
    for d in sorted(src.glob("[0-9]-*")):
        if not (d / ".git").exists():
            continue
        files = {}
        for rel in SPACE_MARKERS:
            r = subprocess.run(["git", "-C", str(d), "show", f"HEAD:{rel}"], capture_output=True,
                               text=True, timeout=60)
            if r.returncode == 0:
                files[rel] = r.stdout
        spaces.append({"dir": d.name, "system": d.name == system, "files": files})
        if d.name == system:
            try:
                _archive(d, seed / "system", "1-tracks/dev/datacore-upgrade/promises")
            except RuntimeError:
                pass
    (seed / "spaces.json").write_text(json.dumps(spaces, indent=1))
    return seed


# ═════════════════════════════════════════════════════════════════════════════
# The sandbox fleet (Linux side)
# ═════════════════════════════════════════════════════════════════════════════

def libfaketime() -> str | None:
    found = sorted(glob.glob("/usr/lib/*/faketime/libfaketime.so.1") +
                   glob.glob("/usr/lib/faketime/libfaketime.so.1"))
    return found[0] if found else None


@dataclasses.dataclass
class Options:
    seed: Path
    out: Path
    days: int = 7
    start: dt.date = dt.date(2026, 10, 1)
    faults: list | None = None          # None = the default week of faults
    roster: Path | None = None          # override the seed's roster (tests)
    manifest: Path | None = None        # override every machine's job list (tests)
    spaces: list | None = None          # limit the spaces (bare names)
    evals: str = "changed"              # off | once | changed | nightly
    min_interval_s: int = 3600
    job_timeout_s: int = 300
    workdir: Path | None = None
    keep_logs: bool = True


@dataclasses.dataclass
class Machine:
    name: str
    kind: str
    alias: str | None
    actor: str
    manifest_name: str
    home: Path

    @property
    def visitor(self) -> bool:
        return self.kind == "workstation"

    @property
    def data(self) -> Path:
        return self.home / "Data"


def _load_doc(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text()) or {}


class Fleet:
    def __init__(self, opts: Options):
        self.o = opts
        self.root = Path(opts.workdir or tempfile.mkdtemp(prefix="fleet-sim-")).resolve()
        self.remote = self.root / "remote"
        self.bin = self.root / "bin"
        self.state = self.root / "state"
        self.fake = libfaketime()
        if not self.fake:
            raise SystemExit("libfaketime not found: run inside the simulator container "
                             "(fleet_week_sim.py docker)")
        roster_src = opts.roster or (opts.seed / "config" / "infrastructure.yaml")
        self.roster = _load_doc(roster_src)
        self.roles = self.roster.get("roles") or {}
        self.machines: dict[str, Machine] = {}
        for name, cfg in (self.roster.get("servers") or {}).items():
            access = cfg.get("access") or {}
            alias = cfg.get("ssh_alias")
            self.machines[name] = Machine(
                name=name, kind=str(cfg.get("kind") or "server"),
                alias=None if alias in (None, "", "-") else str(alias),
                actor=str(access.get("actor") or (cfg.get("ledger_actors") or [name])[0]),
                manifest_name=str(cfg.get("manifest_machine") or name),
                home=self.root / "m" / name / "home")
        self.offline: set[str] = set()
        self.spaces = self._space_list()
        self._jobs_cache: dict = {}
        self.hardcoded_home: dict | None = None
        self.notes: list[str] = []
        #: Built inside a job of an outer run (the audit job runs this file's tests):
        #: the container-wide /home/<user> link, /etc/hosts and :22 belong to it.
        self.nested = bool(os.environ.get("SIM_ROOT"))
        self.ports_modelled = False
        self.ips: dict[str, str] = {}

    # -- roles ------------------------------------------------------------------
    def role_machine(self, role: str) -> str | None:
        """A machine by its duty, never by its name (INS-3)."""
        if role in self.machines:
            return role
        if role == "workstation":
            return next((m for m, x in self.machines.items() if x.visitor), None)
        if role == "spare":
            taken = set()
            for v in self.roles.values():
                taken.update(v if isinstance(v, list) else [v])
            spare = [m for m, x in self.machines.items() if not x.visitor and m not in taken]
            return spare[-1] if spare else None
        v = self.roles.get(role)
        v = v[0] if isinstance(v, list) and v else v
        if v in self.machines:
            return v
        for m, x in self.machines.items():   # a role may name an actor
            if x.actor == v:
                return m
        return None

    def _space_list(self) -> list[dict]:
        try:
            spaces = json.loads((self.o.seed / "spaces.json").read_text())
        except (OSError, ValueError):
            spaces = []
        if not spaces:
            spaces = [{"dir": "0-personal", "system": True, "files": {}}]
        if self.o.spaces:
            want = set(self.o.spaces)
            spaces = [s for s in spaces if s["dir"].split("-", 1)[-1] in want or s["dir"] in want]
        return spaces

    def space_dir(self, role: str) -> str | None:
        """The folder of the space holding `role` in install.yaml, or by bare name."""
        if role == "system":
            s = next((s for s in self.spaces if s.get("system")), None)
            if s:
                return s["dir"]
        try:
            roles = _load_doc(self.o.seed / "config" / "install.yaml").get("roles") or {}
        except OSError:
            roles = {}
        name = roles.get(role, role)
        name = name[0] if isinstance(name, list) else name
        for s in self.spaces:
            if s["dir"].split("-", 1)[-1] == str(name) or s["dir"] == str(name):
                return s["dir"]
        return self.spaces[0]["dir"] if self.spaces else None

    # -- the clock ----------------------------------------------------------------
    def env(self, m: Machine, ts: dt.datetime, job: str = "") -> dict:
        offset = int(ts.timestamp() - time.time())
        return {
            "HOME": str(m.home), "USER": "sim", "LOGNAME": "sim", "SHELL": "/bin/bash",
            "PATH": f"{self.bin}:/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8", "TZ": "UTC",
            "LD_PRELOAD": self.fake, "FAKETIME": f"{offset:+d}", "NO_FAKE_STAT": "1",
            # "0", explicitly: set to 1, or left unset, libfaketime makes every
            # time.sleep() in python raise EINVAL (measured in this image).
            "FAKETIME_DONT_FAKE_MONOTONIC": "0", "FAKETIME_NO_CACHE": "1",
            "GIT_CONFIG_GLOBAL": str(m.home / ".gitconfig"), "GIT_CONFIG_SYSTEM": "/dev/null",
            "SIM_ROOT": str(self.root), "SIM_STATE": str(self.state), "SIM_MACHINE": m.name,
            "SIM_JOB": job, "PYTHONDONTWRITEBYTECODE": "1",
            **SANDBOX_ENV,
        }

    def sh(self, m: Machine, cmd: str, ts: dt.datetime, *, job: str = "", cwd: Path | None = None,
           timeout: int | None = None) -> tuple[int, str, float]:
        started = time.monotonic()
        limit = timeout or self.o.job_timeout_s
        # Its own process group, so a timeout kills everything the job started
        # (a killed bash used to leave its python children polling on).
        proc = subprocess.Popen(["bash", "-c", cmd], env=self.env(m, ts, job), cwd=str(cwd or m.home),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                text=True, errors="replace", start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=limit)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            import signal
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            out, _ = proc.communicate()
            rc, out = 124, (out or "") + f"\n[fleet-sim] TIMEOUT after {limit}s (job and its children killed)"
        return rc, out or "", time.monotonic() - started

    def settle_mtimes(self, marker: Path, ts: dt.datetime) -> None:
        """Give every file a job just wrote the simulated time as its mtime.

        libfaketime fakes the clock, not the kernel's file timestamps (and stat
        faking is off, NO_FAKE_STAT, so an offset cannot leak into ages). Files
        whose ctime moved since `marker` are this step's writes.
        """
        stamp = ts.timestamp()
        r = subprocess.run(["find", str(self.root / "m"), "-name", ".git", "-prune", "-o",
                            "-cnewer", str(marker), "-print0"], capture_output=True, timeout=300)
        for raw in r.stdout.split(b"\0"):
            if raw:
                try:
                    os.utime(raw, (stamp, stamp), follow_symlinks=False)
                except OSError:
                    pass

    # -- building -----------------------------------------------------------------
    def _seed_repo(self, name: str, tree: Path | None, ts: dt.datetime, extra: dict | None = None) -> Path:
        work = self.root / "seedwork" / name
        if work.exists():
            shutil.rmtree(work)
        if tree is not None:
            shutil.copytree(tree, work, symlinks=True)
        else:
            work.mkdir(parents=True)
        for rel, text in (extra or {}).items():
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_text(text)
        bare = self.remote / f"{name}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
               "LD_PRELOAD": self.fake, "FAKETIME": f"{int(ts.timestamp() - time.time()):+d}"}
        for args in (["init", "-q", "-b", "main"], ["add", "-A", "-f", "."],
                     ["-c", "user.name=seed", "-c", "user.email=seed@fleet-sim",
                      "commit", "-qm", "fleet-sim seed", "--allow-empty"]):
            subprocess.run(["git", "-C", str(work), *args], env=env, capture_output=True, check=True,
                           timeout=300)
        subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], env=env,
                       capture_output=True, check=True, timeout=300)
        shutil.rmtree(work)
        return bare

    def build(self) -> None:
        t0 = dt.datetime.combine(self.o.start, dt.time(), tzinfo=UTC) - dt.timedelta(hours=6)
        for d in (self.remote, self.bin, self.state):
            d.mkdir(parents=True, exist_ok=True)
        # The shared origins: core, every module repository, every space.
        core_tree = self.o.seed / "core-src"
        self._seed_repo("core", core_tree, t0)
        mods = sorted(p for p in (self.o.seed / "modules").iterdir()) if (self.o.seed / "modules").is_dir() else []
        for mod in mods:
            self._seed_repo(f"modules/{mod.name}", mod, t0)
        for s in self.spaces:
            extra = dict(s.get("files") or {})
            extra.setdefault("org/inbox.org", _fixture_inbox(s["dir"]))
            extra["README.md"] = f"# {s['dir']} (fleet-sim scratch space)\n"
            extra.setdefault(".datacore/events/.gitkeep", "")
            tree = None
            if s.get("system") and (self.o.seed / "system").is_dir():
                tree = self.o.seed / "system"
            self._seed_repo(f"spaces/{s['dir']}", tree, t0, extra)

        # The stand-ins on PATH (copied: the seed is mounted read-only).
        for src, names in (("stand_in.py", MODEL_RUNTIMES), ("host_stubs.py", HOST_TOOLS)):
            impl = self.root / "stubs" / src
            impl.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SIMDIR / src, impl)
            impl.chmod(0o755)
            for name in names:
                _link(impl, self.bin / name)

        manifest_doc = _load_doc(self.o.manifest) if self.o.manifest else None
        for m in self.machines.values():
            self._build_machine(m, mods, manifest_doc, t0)
        fleet = {"aliases": {}, "machines": {}}
        for m in self.machines.values():
            for n in {m.name, m.alias, m.manifest_name} - {None}:
                fleet["aliases"][n] = m.name
            env = self.env(m, t0)
            fleet["machines"][m.name] = {"env": {k: env[k] for k in ("HOME", "GIT_CONFIG_GLOBAL",
                                                                   "GIT_CONFIG_SYSTEM", "PATH")}}
        (self.root / "fleet.json").write_text(json.dumps(fleet, indent=1))
        self._serve_ports()
        self.write_offline()
        self._map_hardcoded_home()
        for m in self.machines.values():
            self._host_setup(m, t0)

    def _host_setup(self, m: Machine, ts: dt.datetime) -> None:
        """Build an agent machine the way a real one is built: through the
        add-a-machine installer (INS-7), so what it wires -- the safety guard in
        the user settings, the git hooks, signing -- is what the sandbox runs
        with. The workstation is the owner's and has no such installer."""
        script = m.data / ".datacore" / "lib" / "agent_host_setup.sh"
        if m.visitor or not script.is_file():
            return
        rc, out, _ = self.sh(m, f"DATACORE_RUNNER={shlex.quote(str(m.data))} "
                                f"DATACORE_STATE={shlex.quote(str(m.home / '.datacore' / 'state'))} "
                                f"bash {shlex.quote(str(script))} --host {shlex.quote(m.name)}", ts, timeout=240)
        fails = [l.split("FAIL", 1)[1].strip()[:90] for l in out.splitlines() if "] FAIL" in l]
        self.notes.append(f"host setup on {m.name}: agent_host_setup.sh rc={rc}"
                          + (f"; {len(fails)} check(s) it could not pass here: " + " | ".join(fails[:6])
                             if fails else "; every check passed"))

    def _serve_ports(self) -> None:
        """Give each machine a loopback address under its roster names in
        /etc/hosts, and answer on :22 there while it is online -- what the fleet
        probe asks of a real host. Only as root in the container, and never from
        a fleet nested in an outer run's job (those names and ports are its)."""
        hosts = Path("/etc/hosts")
        if self.nested or not hasattr(os, "geteuid") or os.geteuid() != 0 or not os.access(hosts, os.W_OK):
            return
        text = hosts.read_text()
        existing: dict[str, str] = {}
        for line in text.splitlines():
            parts = line.split("#", 1)[0].split()
            for n in parts[1:]:
                existing.setdefault(n, parts[0])
        add, self.ips = [], {}
        for i, m in enumerate(sorted(self.machines.values(), key=lambda x: x.name)):
            names = sorted({m.name, m.alias, m.manifest_name} - {None})
            ip = next((existing[n] for n in names if existing.get(n, "").startswith("127.0.0.")),
                      f"127.0.0.{10 + i}")
            self.ips[m.name] = ip
            new = [n for n in names if existing.get(n) != ip]
            if new:
                add.append(f"{ip} {' '.join(new)}  # fleet-sim")
        try:
            if add:
                hosts.write_text(text.rstrip("\n") + "\n" + "\n".join(add) + "\n")
        except OSError as exc:
            self.notes.append(f"machines not given addresses ({exc}): the fleet probe is not modelled")
            return
        self.ports_modelled = True
        self.notes.append("each machine answers on :22 at its roster names (" + ", ".join(
            f"{k}={v}" for k, v in sorted(self.ips.items())) + ") while it is online, so the fleet probe runs")

    def _sync_ports(self) -> None:
        import socket
        import threading
        for m in self.machines.values():
            ip = self.ips.get(m.name)
            if ip is None:
                continue
            sock = _LISTENERS.get(ip)
            if m.name in self.offline and sock is not None:
                _LISTENERS.pop(ip, None)
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
            elif m.name not in self.offline and sock is None:
                sock = socket.socket()
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind((ip, 22))
                except OSError as exc:
                    sock.close()
                    self.notes.append(f"{m.name}: could not answer on {ip}:22 ({exc})")
                    continue
                sock.listen(16)
                _LISTENERS[ip] = sock

                def serve(s=sock):
                    while True:
                        try:
                            conn, _ = s.accept()
                        except OSError:
                            return
                        conn.close()
                threading.Thread(target=serve, daemon=True).start()

    def _map_hardcoded_home(self) -> None:
        """Scripts that name their host's home literally (`/home/<user>/Data`)
        escape any other checkout. Point that one path at the always-on
        machine's sandbox home -- the host those scripts are deployed to --
        and say so in the report. Found from the scripts, never named here."""
        box = self.machines.get(self.role_machine("always_on") or "")
        if box is None:
            return
        if self.nested:
            self.notes.append("built inside another simulated machine (a job ran this harness's own tests): "
                              "/home/<user> and the host names are the outer run's and are left alone")
            return
        counts: dict[str, int] = {}
        for f in (box.data / ".datacore" / "lib").glob("cos_*.sh"):
            for user in re.findall(r"/home/([a-z_][a-z0-9_-]*)/", f.read_text(errors="replace")):
                counts[user] = counts.get(user, 0) + 1
        if not counts:
            return
        user = max(counts, key=counts.get)
        target = Path("/home") / user
        if target.exists() and not target.is_symlink():
            if any(target.iterdir()):
                self.notes.append(f"{target} exists in the container and is not empty; not mapped")
                return
            target.rmdir()
        target.parent.mkdir(parents=True, exist_ok=True)
        _link(box.home, target)
        self.hardcoded_home = {"path": str(target), "machine": box.name, "mentions": counts[user]}
        self.notes.append(f"{counts[user]} literal mentions of {target}/ in the always-on host's cos_*.sh "
                          f"scripts: that path is mapped to {box.name}'s sandbox home, or their output "
                          f"would land outside every machine")

    def _clone(self, m: Machine, remote: str, dest: Path, ts: dt.datetime) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        rc, out, _ = self.sh(m, f"git clone -q {shlex.quote(str(self.remote / remote))} {shlex.quote(str(dest))}",
                             ts, timeout=300)
        if rc:
            raise RuntimeError(f"clone {remote} for {m.name}: {out[-300:]}")

    def _install_core(self, m: Machine, dest: Path, mods: list[Path], manifest_doc: dict | None,
                      ts: dt.datetime) -> None:
        import yaml
        self._clone(m, "core.git", dest, ts)
        for mod in mods:
            self._clone(m, f"modules/{mod.name}.git", dest / ".datacore" / "modules" / mod.name, ts)
        cfg = self.o.seed / "config"
        for name, rel in CONFIG_FILES.items():
            src = cfg / name
            if name == "infrastructure.yaml" and self.o.roster:
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                (dest / rel).write_text(yaml.safe_dump(self.roster, sort_keys=False))
                continue
            if name == "manifest.local.yaml" and manifest_doc is not None:
                (dest / rel).write_text(yaml.safe_dump(manifest_doc, sort_keys=False))
                continue
            if src.exists():
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest / rel)

    def _build_machine(self, m: Machine, mods: list[Path], manifest_doc: dict | None,
                       ts: dt.datetime) -> None:
        m.home.mkdir(parents=True, exist_ok=True)
        for d in (".datacore/cos", ".datacore/traces", ".datacore/logs", ".claude"):
            (m.home / d).mkdir(parents=True, exist_ok=True)
        # Private, as file_utils.private_state_directory creates it on a host;
        # a 0755 state directory is refused by the ledger (correctly).
        (m.home / ".datacore" / "state").mkdir(mode=0o700, exist_ok=True)
        (m.home / ".datacore" / "state").chmod(0o700)
        (m.home / ".datacore" / "identity.env").write_text(f"DATACORE_ACTOR={m.actor}\n")
        hooks = m.data / ".datacore" / "githooks"
        (m.home / ".gitconfig").write_text(
            f"[user]\n\tname = {m.actor}\n\temail = {m.actor}@fleet-sim\n"
            f"[init]\n\tdefaultBranch = main\n[safe]\n\tdirectory = *\n"
            f"[core]\n\thooksPath = {hooks}\n")
        self._install_core(m, m.data, mods, manifest_doc, ts)
        # Checkouts a host's jobs name besides ~/Data (the agent hosts' runner).
        cmds = " ".join(str(j.get("cmd") or "") for j in self.jobs_for(m, doc_only=True))
        for extra in (".datacore/v2-runner", ".datacore/audit-src/datacore"):
            if extra in cmds:
                self._install_core(m, m.home / extra, mods, manifest_doc, ts)
        for s in self.spaces:
            self._clone(m, f"spaces/{s['dir']}.git", m.data / s["dir"], ts)
        if m.name == self.role_machine("always_on"):
            self._deploy_cos(m)

    def _deploy_cos(self, m: Machine) -> None:
        """What chief-of-staff's server/deploy.sh ships onto the always-on host."""
        lib = m.data / ".datacore" / "modules" / "chief-of-staff" / "server" / "lib"
        dest = m.data / ".datacore" / "lib"
        if not lib.is_dir():
            return
        for pat in ("cos_*", "winston_*", "miles_delivery.*", "lens_analyze.py", "ws_chat_probe.py"):
            for f in lib.glob(pat):
                if f.is_file():
                    shutil.copy2(f, dest / f.name)
                    if f.suffix in (".sh", ".py"):   # deploy.sh chmods what it ships
                        (dest / f.name).chmod(0o755)
        if (lib / "agent-bin").is_dir():
            shutil.copytree(lib / "agent-bin", dest / "agent-bin", dirs_exist_ok=True)

    def write_offline(self) -> None:
        (self.state / "offline.json").write_text(json.dumps(sorted(self.offline)))
        if self.ports_modelled:
            self._sync_ports()

    # -- the job list -------------------------------------------------------------
    def jobs_for(self, m: Machine, doc_only: bool = False) -> list[dict]:
        """This machine's effective job list, read from ITS checkout (so a parallel
        session's edit there is what runs, as cron would run it)."""
        if doc_only:
            doc = _load_doc(self.o.manifest) if self.o.manifest else None
            if doc is None:
                docs = [_load_doc(self.o.seed / "core-src" / ".datacore/lib/jobs/manifest.yaml")]
                local = self.o.seed / "config" / "manifest.local.yaml"
                if local.exists():
                    docs.append(_load_doc(local))
                by = {}
                for d in docs:
                    for j in d.get("jobs") or []:
                        by[j.get("name")] = j
                doc = {"jobs": list(by.values())}
            return [j for j in doc.get("jobs") or [] if j.get("machine") == m.manifest_name]
        key = self._manifest_key(m)
        cached = self._jobs_cache.get(m.name)
        if cached and cached[0] == key:
            return cached[1]
        script = ("import json,sys; sys.path.insert(0, sys.argv[1]);"
                  "from pathlib import Path; from jobs.manifest import effective_doc;"
                  "p=Path(sys.argv[1])/'jobs'/'manifest.local.yaml';"
                  "p=p if p.exists() else Path(sys.argv[1])/'jobs'/'manifest.yaml';"
                  "print(json.dumps(effective_doc(p), default=str))")
        lib = m.data / ".datacore" / "lib"
        r = subprocess.run([sys.executable, "-c", script, str(lib)], capture_output=True, text=True,
                           env={**os.environ, "HOME": str(m.home), "DATACORE_ROOT": str(m.data)},
                           timeout=120)
        try:
            doc = json.loads(r.stdout)
        except ValueError:
            doc = {"jobs": self.jobs_for(m, doc_only=True)}
            self.notes.append(f"{m.name}: could not read its effective job list "
                              f"({(r.stderr or '').strip()[-200:]}); used the seed's")
        jobs = [j for j in doc.get("jobs") or [] if j.get("machine") == m.manifest_name]
        self._jobs_cache[m.name] = (key, jobs)
        return jobs

    def _manifest_key(self, m: Machine) -> str:
        parts = []
        for rel in ("manifest.yaml", "manifest.local.yaml"):
            p = m.data / ".datacore" / "lib" / "jobs" / rel
            try:
                st = p.stat()
                parts.append(f"{st.st_mtime_ns}:{st.st_size}")
            except OSError:
                parts.append("-")
        return "|".join(parts)


def _fixture_inbox(space: str) -> str:
    """Two open captures WITH ids. A heading that reaches several hosts without
    an id gets a different random id on each, and their next converge
    conflicts -- a real hazard, listed in the report, but not the steady
    state a week of simulation should start from."""
    import uuid
    out = []
    for n, title in enumerate(("Read the fleet week report", "Renew the domain before it lapses",
                               "Summarise this week's fleet report in three lines :AI:research:")):
        iid = uuid.uuid5(uuid.NAMESPACE_URL, f"fleet-sim/{space}/{n}")
        out.append(f"* TODO {title}\n:PROPERTIES:\n:ID: {iid}\n:END:\n")
    return "".join(out)


def _link(target: Path, link: Path) -> None:
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(target)


# ═════════════════════════════════════════════════════════════════════════════
# Faults: the classes met 2026-09-26..30
# ═════════════════════════════════════════════════════════════════════════════

#: Machines are named by duty (roster roles), jobs by pattern, spaces by role.
DEFAULT_FAULTS = [
    {"id": "F1", "kind": "stray_file", "machine": "executor", "day": 2, "at": "01:30",
     "until": [3, "00:00"], "desc": "hand-copied file staged in the core repo, plus an ignored backup "
     "copy of the local job list (2026-09-29 and 09-30 on the overnight host)",
     "expect": "the overnight run's git preflight refuses and a check says so"},
    {"id": "F2", "kind": "pending_work", "machine": "always_on", "space": "personal", "day": 2,
     "at": "04:50", "count": 5, "desc": "five of the host's own ledger events written, not yet committed",
     "expect": "(setup for F3)"},
    {"id": "F3", "kind": "executor_mode", "machine": "always_on", "job": "inbox", "mode": "stash",
     "space": "personal", "day": 2, "at": "04:55", "until": [2, "06:00"],
     "desc": "the inbox job's agent runs `git stash` in the personal space (2026-09-30 05:12 on the box)",
     "expect": "the tool-policy guard refuses; if it did not, the ledger verifier goes red"},
    {"id": "F4", "kind": "executor_mode", "machine": "always_on", "job": "*", "mode": "expired_login",
     "day": 3, "at": "00:00", "until": [4, "00:00"],
     "desc": "the always-on host's model login expired for a day (the NotebookLM login, 2026-09-30)",
     "expect": "the model-driven jobs fail and job_verify says which"},
    {"id": "F5", "kind": "replace_job_cmd", "machine": "executor", "job": "github-triage$",
     "with": "github-triage-report", "day": 3, "at": "01:00",
     "desc": "a parallel session replaces the nightly GitHub capture with the read-only report "
     "(2026-09-29)", "expect": "a check notices the capture stopped (CAP-4/TSK-3 went red for real)"},
    {"id": "F6", "kind": "offline", "machine": "spare", "day": 4, "at": "00:00", "until": [5, "00:00"],
     "desc": "one agent host offline for a day", "expect": "the always-on host's fleet probe reports it DOWN"},
    {"id": "F7", "kind": "executor_mode", "machine": "always_on", "job": "research", "mode": "usage_limit",
     "day": 5, "at": "05:25", "until": [5, "06:00"],
     "desc": "the weekly usage limit hit by one job (research, 2026-09-30)",
     "expect": "that job's check goes red"},
    {"id": "F8", "kind": "push_conflict", "machine": "executor", "other": "always_on", "space": "system",
     "day": 5, "at": "05:50", "desc": "two hosts commit different versions of one file; one pushes first",
     "expect": "the second host's sync reports the conflict instead of losing either side"},
    {"id": "F9", "kind": "executor_mode", "machine": "always_on", "job": "inbox$", "mode": "hand_edit",
     "space": "personal", "day": 6, "at": "04:55", "until": [6, "06:00"],
     "desc": "an agent hand-edits another writer's event log and commits it",
     "expect": "the ledger write gate refuses the commit, or the verifier goes red"},
    {"id": "F10", "kind": "executor_mode", "machine": "executor", "job": "github-triage", "mode": "reset",
     "space": "core", "day": 6, "at": "03:10", "until": [6, "04:00"],
     "desc": "an agent runs `git reset --hard` in the core checkout", "expect": "the guard refuses"},
    {"id": "F11", "kind": "executor_mode", "machine": "always_on", "job": "tomorrow$",
     "mode": "autostash", "space": "personal", "day": 6, "at": "19:55", "until": [6, "21:00"],
     "desc": "an agent pulls with --autostash", "expect": "the guard refuses"},
    {"id": "F12", "kind": "executor_mode", "machine": "always_on", "job": "inbox$", "mode": "claim_done",
     "day": 7, "at": "04:55", "until": [7, "06:00"],
     "desc": "the inbox agent says it is done and processes nothing",
     "expect": "the inbox job or its eval notices nothing was processed"},
]


class Faults:
    def __init__(self, fleet: Fleet, specs: list[dict], start: dt.date):
        self.f = fleet
        self.specs = []
        for s in specs:
            s = dict(s)
            s["start"] = _at(start, s["day"], s["at"])
            u = s.get("until")
            s["end"] = _at(start, u[0], u[1]) if u else None
            s["target"] = fleet.role_machine(s["machine"])
            s["status"] = "pending" if s["target"] else "skipped: no machine holds that role"
            s["applied"] = []
            self.specs.append(s)

    def due(self, ts: dt.datetime) -> None:
        for s in self.specs:
            if s["status"] == "pending" and s["start"] <= ts:
                self._apply(s, ts)
            elif s["status"] == "active" and s["end"] and s["end"] <= ts:
                self._revert(s, ts)

    def mentions(self, machine: str, job: str, target: str) -> bool:
        m = self.f.machines.get(machine)
        t = self.f.machines.get(target)
        if m is None or t is None:
            return False
        cmd = next((str(j.get("cmd") or "") for j in self.f.jobs_for(m) if j.get("name") == job), "")
        names = {t.name, t.alias, t.manifest_name} - {None}
        return any(n in cmd for n in names) or "ssh" in cmd or "rsync" in cmd

    def blame(self, machine: str, source: str, subject: str, ts: dt.datetime) -> list[dict]:
        """The faults that could have caused this red, by a narrow rule per kind:
        on the machine it was injected on (any machine for an outage), within a
        day of it, and for a model fault only in the jobs it was set on (plus the
        ledger checks, for the faults that touch the ledger)."""
        out = []
        for s in self.specs:
            if str(s["status"]).startswith("skipped") or s["kind"] == "pending_work" or s["start"] > ts:
                continue
            horizon = (s["end"] or s["start"]) + dt.timedelta(hours=26)
            if s["kind"] != "replace_job_cmd" and ts > horizon:
                continue
            machines = {s["target"]}
            if s["kind"] == "offline" and machine != s["target"]:
                # Elsewhere, only a job that reaches the offline host can break for it.
                if not self.mentions(machine, subject, s["target"]):
                    continue
                machines = {machine}
            if s["kind"] == "push_conflict":
                machines.add(self.f.role_machine(s["other"]))
            if machine not in machines:
                continue
            if s["kind"] == "executor_mode" and s["job"] != "*":
                ledgerish = s["mode"] in ("stash", "autostash", "reset", "hand_edit") and source.startswith("ledger")
                if subject not in (s.get("jobs") or []) and not ledgerish:
                    continue
            if s["kind"] == "replace_job_cmd" and subject != (s.get("replaced") or "") \
                    and source != "promise eval":
                continue
            out.append(s)
        return out

    # -- application ------------------------------------------------------------
    def _card(self, machine: str) -> tuple[Path, dict]:
        p = self.f.state / f"executor-{machine}.json"
        try:
            return p, json.loads(p.read_text())
        except (OSError, ValueError):
            return p, {"default": "ok", "jobs": {}}

    def _space_path(self, m: Machine, role: str | None) -> Path:
        if role == "core":
            return m.data
        d = self.f.space_dir(role or "personal")
        return m.data / d if d else m.data

    def _apply(self, s: dict, ts: dt.datetime) -> None:
        m = self.f.machines[s["target"]]
        kind = s["kind"]
        try:
            if kind == "executor_mode":
                p, card = self._card(m.name)
                spec = {"mode": s["mode"]}
                if s.get("space"):
                    spec["where"] = str(self._space_path(m, s["space"]))
                if s["job"] == "*":
                    card["default"] = spec
                else:
                    names = [j["name"] for j in self.f.jobs_for(m) if re.search(s["job"], j["name"])]
                    if not names:
                        s["status"] = f"skipped: no job on {m.name} matches {s['job']!r}"
                        return
                    for n in names:
                        card.setdefault("jobs", {})[n] = spec
                    s["jobs"] = names
                p.write_text(json.dumps(card))
            elif kind == "stray_file":
                core = m.data
                victim = next((p for p in sorted((core / ".datacore" / "lib").glob("*.sh"))), None)
                bak = core / ".datacore" / "lib" / (victim.name + ".orig" if victim else "copied.sh")
                shutil.copy2(victim, bak) if victim else bak.write_text("#!/bin/sh\n")
                self.f.sh(m, f"git -C {shlex.quote(str(core))} add -f -- {shlex.quote(str(bak))}", ts)
                ign = core / ".datacore" / "lib" / "jobs" / f"manifest.local.yaml.bak-{ts:%Y%m%d}-sim"
                local = core / ".datacore" / "lib" / "jobs" / "manifest.local.yaml"
                if local.exists():
                    shutil.copy2(local, ign)
                s["applied"] = [str(bak), str(ign)]
            elif kind == "pending_work":
                space = self._space_path(m, s.get("space"))
                cli = m.data / ".datacore" / "lib" / "ledger_cli.py"
                for i in range(int(s.get("count") or 5)):
                    payload = json.dumps({"id": f"sim-pending-{ts:%m%d}-{i}", "title": f"pending work {i}"})
                    rc, out, _ = self.f.sh(m, f"python3 {shlex.quote(str(cli))} append --space "
                                              f"{shlex.quote(str(space))} --type item.create --payload "
                                              f"{shlex.quote(payload)}", ts)
                    s["applied"].append(f"append rc={rc} {out.strip()[-120:]}")
            elif kind == "replace_job_cmd":
                import yaml
                jobs = self.f.jobs_for(m)
                a = next((j for j in jobs if re.search(s["job"], j["name"])), None)
                b = next((j for j in jobs if re.search(s["with"], j["name"]) and j is not a), None)
                if not a or not b:
                    s["status"] = f"skipped: {s['job']!r} or {s['with']!r} not on {m.name}"
                    return
                local = m.data / ".datacore" / "lib" / "jobs" / "manifest.local.yaml"
                doc = _load_doc(local) if local.exists() else {"version": 1, "jobs": []}
                entry = next((j for j in doc.get("jobs") or [] if j.get("name") == a["name"]), None)
                if entry is None:
                    entry = {k: v for k, v in a.items()}
                    doc.setdefault("jobs", []).append(entry)
                entry["cmd"] = b["cmd"]
                local.write_text(yaml.safe_dump(doc, sort_keys=False))
                s["applied"] = [f"{a['name']} now runs: {b['cmd'][:120]}"]
                s["replaced"] = a["name"]
            elif kind == "offline":
                self.f.offline.add(m.name)
                self.f.write_offline()
            elif kind == "push_conflict":
                other = self.f.machines.get(self.f.role_machine(s["other"]) or "")
                if other is None:
                    s["status"] = "skipped: no second machine"
                    return
                for who, text in ((m, "first"), (other, "second")):
                    sp = self._space_path(who, s.get("space"))
                    rel = "org/fleet-sim-shared.org"
                    (sp / rel).write_text(f"* Shared note\nversion written on {who.name} ({text})\n")
                    rc, out, _ = self.f.sh(who, f"cd {shlex.quote(str(sp))} && git add {rel} && "
                                                f"git commit -qm 'edit shared note on {who.name}' -- {rel}"
                                                + (" && git push -q origin HEAD" if who is m else ""), ts)
                    s["applied"].append(f"{who.name}: rc={rc} {out.strip()[-160:]}")
            s["status"] = "active" if s.get("end") else "applied"
        except Exception as exc:  # noqa: BLE001 -- a fault that cannot be injected is reported
            s["status"] = f"skipped: {type(exc).__name__}: {exc}"

    def _revert(self, s: dict, ts: dt.datetime) -> None:
        m = self.f.machines[s["target"]]
        if s["kind"] == "executor_mode":
            p, card = self._card(m.name)
            if s["job"] == "*":
                card["default"] = "ok"
            for n in s.get("jobs") or []:
                (card.get("jobs") or {}).pop(n, None)
            p.write_text(json.dumps(card))
        elif s["kind"] == "stray_file":
            for path in s.get("applied") or []:
                self.f.sh(m, f"git -C {shlex.quote(str(m.data))} rm -q --cached --ignore-unmatch -- "
                             f"{shlex.quote(path)}; rm -f {shlex.quote(path)}", ts)
        elif s["kind"] == "offline":
            self.f.offline.discard(m.name)
            self.f.write_offline()
        s["status"] = "ended"


def _at(start: dt.date, day: int, hhmm: str) -> dt.datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return dt.datetime.combine(start + dt.timedelta(days=int(day) - 1), dt.time(h, m), tzinfo=UTC)


# ═════════════════════════════════════════════════════════════════════════════
# The checks after each night
# ═════════════════════════════════════════════════════════════════════════════

_JOB_FAIL = re.compile(r"^job '([^']+)' FAILED:\s*$")


def redirect_target(cmd: str, home: Path) -> Path | None:
    """The file a job's cmd appends its output to (`>> ~/x.log`), if any: since
    the job list carries the crontab's redirect, a failing job's cause is there."""
    hits = re.findall(r">>\s*(\S+)", cmd)
    if not hits:
        return None
    t = hits[-1].strip("'\"")
    if t.startswith("~/"):
        return home / t[2:]
    if t.startswith("$HOME/"):
        return home / t[6:]
    return Path(t) if t.startswith("/") else None


def log_size(path: Path | None) -> int:
    try:
        return path.stat().st_size if path else 0
    except OSError:
        return 0


def appended_since(path: Path | None, size: int) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(size)
            return fh.read()[-20000:].decode(errors="replace")
    except (OSError, TypeError):
        return ""


def _first_line(text: str) -> str:
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    pat = re.compile(r"error|fail|refus|denied|not found|no such|traceback|fatal|stale|missing|"
                     r"invalid|cannot|could not|timeout|limit", re.I)
    for l in lines:
        if pat.search(l) and not l.startswith("[cos-route]"):
            return l[:240]
    return (lines[-1] if lines else "")[:240]


def job_verify(fleet: Fleet, m: Machine, ts: dt.datetime) -> list[dict]:
    cmd = (f"python3 {shlex.quote(str(m.data / '.datacore/lib/job_verify.py'))} --machine "
           f"{shlex.quote(m.manifest_name)} --no-emit --alert log")
    rc, out, _ = fleet.sh(m, cmd, ts, job="__job_verify__", timeout=600)
    found, cur = [], None
    for line in out.splitlines():
        hit = _JOB_FAIL.match(line.strip())
        if hit:
            cur = {"source": "job_verify", "subject": hit.group(1), "first_check": ""}
            found.append(cur)
        elif cur is not None and line.startswith("  - ") and not cur["first_check"]:
            cur["first_check"] = line[4:].strip()[:300]
    if rc and not found:
        found.append({"source": "job_verify", "subject": "(verifier)", "first_check": _first_line(out)})
    return found


def ledger_checks(fleet: Fleet, m: Machine, ts: dt.datetime) -> list[dict]:
    cli = shlex.quote(str(m.data / ".datacore/lib/ledger_cli.py"))
    found = []
    for s in fleet.spaces:
        sp = m.data / s["dir"]
        if not (sp / ".datacore" / "events").is_dir():
            continue
        q = shlex.quote(str(sp))
        rc, out, _ = fleet.sh(m, f"python3 {cli} verify --space {q}", ts, job="__ledger__", timeout=300)
        if rc:
            found.append({"source": "ledger verify", "subject": s["dir"], "first_check": _first_line(out)})
        rc, out, _ = fleet.sh(m, f"python3 {cli} stopped --space {q}", ts, job="__ledger__", timeout=300)
        if rc:
            found.append({"source": "ledger stopped", "subject": s["dir"], "first_check": _first_line(out)})
    return found


def promise_evals(fleet: Fleet, m: Machine, ts: dt.datetime, out_dir: Path) -> tuple[list[dict], dict]:
    """Every promise eval the sandbox can run, on the machine that runs the scoreboard.

    Collected the way an ordinary pytest run collects them -- the needs gate
    (test-needs.yaml) leaves out what this sandbox cannot provide -- rather
    than with PROMISE_EVALS_ALL, which would count a missing need as red.
    Agent-behaviour evals need a model and are never run here.
    """
    suites = [("root", m.data / ".datacore" / "lib", {})]
    for mod in sorted((m.data / ".datacore" / "modules").glob("*/tests")):
        suites.append((f"module:{mod.parent.name}", mod.parent, {"DATACORE_ROOT": str(m.data)}))
    jobs = []
    for name, cwd, extra in suites:
        files = sorted((cwd / "tests").glob("test_promise_*.py"))
        for i in range(0, len(files), 12):
            jobs.append((name, cwd, extra, files[i:i + 12], len(jobs)))
    results: dict[str, dict] = {}

    def one(job):
        name, cwd, extra, files, n = job
        xml = out_dir / f"evals-{n}.xml"
        env = {**fleet.env(m, ts, "__evals__"), **extra}
        env.pop("PROMISE_EVALS_ALL", None)
        try:
            subprocess.run(["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            f"--junitxml={xml}", *[str(f) for f in files]], cwd=str(cwd), env=env,
                           capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            return {f"{name}:{f.name}": {"status": "red", "why": "timed out after 900s"} for f in files}
        got = {f"{name}:{f.name}": {"status": "not collected", "why": "needs unmet in the sandbox"}
               for f in files}
        try:
            tree = ET.parse(xml)
        except (ET.ParseError, OSError):
            return got
        for case in tree.iter("testcase"):
            fname = (case.get("file") or case.get("classname", "").split(".")[-1] + ".py").split("/")[-1]
            key = f"{name}:{fname}"
            if key not in got:
                continue
            cur = got[key]
            bad = next((c for c in case if c.tag in ("failure", "error")), None)
            if bad is not None and "agent eval not run" in ((bad.get("message") or "") + (bad.text or "")):
                # Needs a real model run (DATACORE_AGENT_EVALS): not runnable here.
                if cur["status"] == "not collected":
                    cur.update(status="needs a model", why="agent-behaviour eval")
                continue
            skipped = any(c.tag == "skipped" for c in case)
            if cur["status"] == "not collected":
                cur.update(status="green", why="")
            if bad is not None and cur["status"] != "red":
                msg = (bad.get("message") or bad.text or "").strip().splitlines()
                cur.update(status="red", why=f"{case.get('name')}: {(msg[0] if msg else '')[:220]}")
            elif skipped and cur["status"] == "green":
                cur.setdefault("skipped", 0)
                cur["skipped"] = cur.get("skipped", 0) + 1
        return got

    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for got in pool.map(one, jobs):
            results.update(got)
    breaks = [{"source": "promise eval", "subject": k, "first_check": v["why"]}
              for k, v in sorted(results.items()) if v["status"] == "red"]
    return breaks, results


# ═════════════════════════════════════════════════════════════════════════════
# The week
# ═════════════════════════════════════════════════════════════════════════════

def _cause_guess(text: str) -> str:
    t = text or ""
    rules = [
        (r"usage limit|rate.?limit|429", "a usage limit refused the model call"),
        (r"Invalid API key|/login|Not logged in|expired", "a login is expired or missing"),
        (r"Could not resolve (host|hostname)|Network is unreachable|Temporary failure in name resolution|"
         r"Connection refused|Failed to connect|getaddrinfo|Name or service not known|urlopen error",
         "needs the network (the sandbox has none)"),
        (r"Connection timed out|port 22", "a host it reaches over ssh is offline"),
        (r"hash chain|chain broken|witness|rewound|seq", "the ledger chain does not verify"),
        (r"authored changes|working tree|index contains|preflight", "a git preflight refused a dirty tree"),
        (r"CONFLICT|non-fast-forward|diverged|rejected", "a git conflict or rejected push"),
        (r"stale|older than|max_age|last modified", "its artifact went stale: the job did not produce it in time"),
        (r"No such file or directory|not found|does not exist|can't open file",
         "a file or command it needs does not exist here (host-only file, or not in any repository)"),
        (r"syntax error", "the manifest's command is not runnable as written"),
        (r"TIMEOUT", "the job hung past the time limit"),
        (r"Traceback|Error:", "the job raised an error"),
    ]
    for pat, guess in rules:
        if re.search(pat, t, re.I):
            return guess
    return "unclassified: see the job log"


class Week:
    def __init__(self, opts: Options):
        self.o = opts
        opts.out.mkdir(parents=True, exist_ok=True)
        self.fleet = Fleet(opts)
        self.faults = Faults(self.fleet, DEFAULT_FAULTS if opts.faults is None else opts.faults, opts.start)
        self.events: list[dict] = []        # every raw failure, night by night
        self.checkpoints: list[dict] = []
        self.runs = 0
        self.daemons: set[str] = set()
        self.not_modelled: dict[str, str] = {}
        self.first_run: dict = {}
        self.first_ok: dict = {}
        self.unknown: set[str] = set()
        self.last_eval_key = None
        self.eval_results: dict = {}

    def night_of(self, ts: dt.datetime) -> int:
        """Night k runs from 09:00 of day k to 09:00 of day k+1; night 0 is install morning."""
        first = dt.datetime.combine(self.o.start, dt.time(*CHECK_AT), tzinfo=UTC)
        return 0 if ts < first else int((ts - first).total_seconds() // 86400) + 1

    def plan(self, day: dt.date) -> list[tuple[dt.datetime, Machine, dict]]:
        out = []
        for m in self.fleet.machines.values():
            for j in self.fleet.jobs_for(m):
                spec = parse_schedule(j.get("schedule"), j.get("trigger"), str(j.get("cmd") or ""))
                rules = NOT_MODELLED if self.fleet.ports_modelled else {**NOT_MODELLED, **NEEDS_PORTS}
                why = next((w for pat, w in rules.items()
                            if re.search(pat, str(j.get("cmd") or ""))), None)
                if why:
                    self.not_modelled[f"{m.name}:{j['name']}"] = why
                    continue
                if spec.kind == "daemon":
                    self.daemons.add(f"{m.name}:{j['name']}")
                    continue
                if spec.kind == "unknown":
                    self.unknown.add(f"{m.name}:{j['name']} ({j.get('schedule')})")
                    continue
                for ts in fires(spec, day, visitor=m.visitor, min_interval_s=self.o.min_interval_s):
                    out.append((ts, m, j))
        out.sort(key=lambda x: (x[0], x[1].name, x[2]["name"]))
        return out

    def run_job(self, ts: dt.datetime, m: Machine, j: dict) -> None:
        # The command as the machine's job list says NOW: a session that edits
        # it mid-day changes what the next firing runs, as it would with cron.
        cur = next((x for x in self.fleet.jobs_for(m) if x.get("name") == j["name"]), None)
        if cur is None:
            return
        j = cur
        target = redirect_target(str(j.get("cmd") or ""), m.home)
        before = log_size(target)
        rc, out, secs = self.fleet.sh(m, str(j.get("cmd") or "true"), ts, job=j["name"])
        if target is not None:
            out = (out + appended_since(target, before)) if out.strip() else appended_since(target, before)
        self.runs += 1
        self.first_run.setdefault((m.name, j["name"]), ts)
        if rc in (j.get("exit_ok") or [0]):
            self.first_ok.setdefault((m.name, j["name"]), ts)
        with open(self.o.out / "runs.jsonl", "a") as fh:
            fh.write(json.dumps({"ts": ts.isoformat(), "machine": m.name, "job": j["name"], "rc": rc,
                                 "secs": round(secs, 2)}) + "\n")
        ok_codes = j.get("exit_ok") or [0]
        night = self.night_of(ts)
        log = None
        if self.o.keep_logs:
            log = self.o.out / "logs" / f"night{night}" / m.name / f"{ts:%m%d-%H%M}-{j['name']}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(f"$ {j.get('cmd')}\n# rc={rc} {secs:.1f}s at {ts.isoformat()}\n\n{out[-20000:]}")
        if rc in ok_codes:
            return
        self.events.append({"night": night, "ts": ts.isoformat(), "machine": m.name,
                            "source": "job exit", "subject": j["name"],
                            "first_check": f"exit {rc}: {_first_line(out)}",
                            "log": str(log.relative_to(self.o.out)) if log else None,
                            "text": out[-2000:]})

    def checkpoint(self, ts: dt.datetime) -> None:
        night = self.night_of(ts) - 1 if ts.time() == dt.time(*CHECK_AT) else self.night_of(ts)
        night = max(night, 0)
        started = time.monotonic()
        eval_m = self._eval_machine()
        found: list[dict] = []
        skipped = sorted(self.fleet.offline)

        def per_machine(m: Machine) -> list[dict]:
            got = job_verify(self.fleet, m, ts) + ledger_checks(self.fleet, m, ts)
            for g in got:
                g["machine"] = m.name
            return got

        online = [m for m in self.fleet.machines.values() if m.name not in self.fleet.offline]
        with cf.ThreadPoolExecutor(max_workers=len(online) or 1) as pool:
            for got in pool.map(per_machine, online):
                found.extend(got)
        found = [f for f in found if not (f["source"] == "job_verify"
                                          and f"{f['machine']}:{f['subject']}" in self.not_modelled)]
        evals_ran = False
        if eval_m is not None and eval_m.name not in self.fleet.offline and self._evals_due(eval_m):
            eb, results = promise_evals(self.fleet, eval_m, ts, self.o.out)
            for b in eb:
                b["machine"] = eval_m.name
            found.extend(eb)
            self.eval_results = results
            self.last_eval_key = self._code_key(eval_m)
            evals_ran = True
        elif self.eval_results and eval_m is not None:
            for k, v in sorted(self.eval_results.items()):
                if v["status"] == "red":
                    found.append({"machine": eval_m.name, "source": "promise eval", "subject": k,
                                  "first_check": v["why"] + " (carried: code unchanged since the last run)"})
        for f in found:
            f.update(night=night, ts=ts.isoformat())
            f.setdefault("text", f.get("first_check", ""))
        self.events.extend(found)
        counts = {}
        if self.eval_results:
            for v in self.eval_results.values():
                counts[v["status"]] = counts.get(v["status"], 0) + 1
        self.checkpoints.append({"night": night, "ts": ts.isoformat(), "found": len(found),
                                 "offline_not_checked": skipped, "evals_ran": evals_ran,
                                 "eval_counts": counts, "secs": round(time.monotonic() - started, 1)})
        print(f"[fleet-sim] check after night {night} ({ts:%a %m-%d %H:%M}): {len(found)} red"
              f"{' (evals ran)' if evals_ran else ''}; {self.runs} job runs so far", flush=True)

    def _eval_machine(self) -> Machine | None:
        """The agent machine whose scoreboard the harness's evals stand in for: the
        executor when it runs one, else the first agent machine that does. Never a
        workstation: since every machine runs a scoreboard (e2acd46, 2026-09-30) the
        first in the roster was the owner's, and the agent-machine evals judged it."""
        if self.o.evals == "off":
            return None
        def scores(m: Machine) -> bool:
            return any("promise_nightly" in str(j.get("cmd") or "") for j in self.fleet.jobs_for(m))
        ex = self.fleet.machines.get(self.fleet.role_machine("executor") or "")
        if ex is not None and not ex.visitor and scores(ex):
            return ex
        for m in self.fleet.machines.values():
            if not m.visitor and scores(m):
                return m
        return ex

    def _code_key(self, m: Machine) -> str:
        return _git_out(m.data, "rev-parse", "HEAD") + _git_out(m.data, "status", "--porcelain")

    def _evals_due(self, m: Machine) -> bool:
        if self.o.evals == "nightly" or self.last_eval_key is None:
            return True
        if self.o.evals == "once":
            return False
        return self._code_key(m) != self.last_eval_key

    def run(self) -> dict:
        t_wall = time.monotonic()
        print(f"[fleet-sim] building a {len(self.fleet.machines)}-machine fleet at {self.fleet.root}", flush=True)
        self.fleet.build()
        print(f"[fleet-sim] built in {time.monotonic() - t_wall:.0f}s", flush=True)
        end = dt.datetime.combine(self.o.start + dt.timedelta(days=self.o.days), dt.time(*CHECK_AT),
                                  tzinfo=UTC)
        day = self.o.start
        marker = self.fleet.root / "mtime.marker"
        while dt.datetime.combine(day, dt.time(), tzinfo=UTC) < end:
            steps = self.plan(day)
            checks = [dt.datetime.combine(day, dt.time(*CHECK_AT), tzinfo=UTC)]
            instants = sorted({ts for ts, _, _ in steps} | set(c for c in checks if c <= end))
            by_ts: dict = {}
            for ts, m, j in steps:
                by_ts.setdefault(ts, []).append((m, j))
            for ts in instants:
                if ts > end:
                    break
                self.faults.due(ts)
                if ts in checks:
                    self.checkpoint(ts)
                group = [(m, j) for m, j in by_ts.get(ts, []) if m.name not in self.fleet.offline]
                if not group:
                    continue
                marker.touch()
                time.sleep(0.01)
                # Every job due this minute starts together, as cron starts them.
                with cf.ThreadPoolExecutor(max_workers=min(len(group), 24)) as pool:
                    list(pool.map(lambda mj: self.run_job(ts, *mj), group))
                self.fleet.settle_mtimes(marker, ts)
            day += dt.timedelta(days=1)
        for name in ("calls.jsonl",):
            if (self.fleet.state / name).exists():
                shutil.copy2(self.fleet.state / name, self.o.out / name)
        # Each machine's own logs and state files, for tracing a break afterwards.
        for m in self.fleet.machines.values():
            for sub in (".datacore/state", ".datacore/cos"):
                src = m.home / sub
                for f in src.rglob("*") if src.is_dir() else []:
                    if f.is_file() and f.suffix in (".log", ".txt", ".json", ".jsonl") and f.stat().st_size < 5_000_000:
                        dest = self.o.out / "machines" / m.name / sub / f.relative_to(src)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, dest)
        report = self.report(time.monotonic() - t_wall)
        return report

    def ever_ok_before(self, ts: dt.datetime) -> set:
        return {k for k, t in self.first_ok.items() if t < ts}

    # -- the report ---------------------------------------------------------------
    def report(self, wall: float) -> dict:
        calls = []
        try:
            calls = [json.loads(l) for l in (self.fleet.state / "calls.jsonl").read_text().splitlines() if l]
        except (OSError, ValueError):
            pass
        first_fault = min((s["start"] for s in self.faults.specs
                           if not str(s["status"]).startswith("skipped")), default=None)
        def sig_of(e):
            return re.sub(r"\d+", "#", e["first_check"].split(" (carried:")[0])[:120]
        red_before = {(e["machine"], e["source"], e["subject"]) for e in self.events
                      if first_fault is None or dt.datetime.fromisoformat(e["ts"]) < first_fault}
        # The same job red for the SAME reason before any fault is baseline; a
        # new reason under a fault is the fault's, even on a job already red.
        sig_before = {(e["machine"], e["source"], e["subject"], sig_of(e)) for e in self.events
                      if first_fault is None or dt.datetime.fromisoformat(e["ts"]) < first_fault}
        groups: dict = {}
        for e in self.events:
            sig = re.sub(r"\d+", "#", e["first_check"].split(" (carried:")[0])[:120]
            key = (e["machine"], e["source"], e["subject"], sig if e["source"] != "job_verify" else "")
            g = groups.setdefault(key, {"machine": e["machine"], "source": e["source"], "subject": e["subject"],
                                        "first_check": e["first_check"], "first_night": e["night"],
                                        "first_ts": e["ts"], "nights": [], "log": e.get("log"),
                                        "text": e.get("text", "")})
            if e["night"] not in g["nights"]:
                g["nights"].append(e["night"])
            g.setdefault("tss", []).append(e["ts"])
        breaks = []
        for g in groups.values():
            ts = dt.datetime.fromisoformat(g["first_ts"])
            active = self.faults.blame(g["machine"], g["source"], g["subject"], ts)
            # A fault that turns an already-red job red again for the same reason
            # (F7 on research, two nights after F4) is credited too: blame is
            # asked for every night the break was seen, not only its first.
            later = []
            for t in g.pop("tss", []):
                for s in self.faults.blame(g["machine"], g["source"], g["subject"], dt.datetime.fromisoformat(t)):
                    if s not in active and s not in later:
                        later.append(s)
            first_run = self.first_run.get((g["machine"], g["subject"]))
            never_ok = (g["machine"], g["subject"]) not in self.ever_ok_before(ts)
            same_reason = (g["machine"], g["source"], g["subject"], sig_of(g)) in sig_before
            baseline = (((g["machine"], g["source"], g["subject"]) in red_before
                         and (same_reason or not active))
                        or (first_run is not None and first_run >= (first_fault or ts) and never_ok
                            and not active))
            guess = _cause_guess(g["text"] + " " + g["first_check"])
            blamed = active + later
            g["faults"] = [s["id"] for s in blamed] if not baseline else []
            if blamed and not baseline:
                ids = ", ".join(g["faults"])
                cause = f"injected ({ids}: {blamed[0]['desc'][:80]}); looks like: {guess}"
                cls = "fault"
            elif baseline:
                cause = (f"baseline, red before any fault was injected: {guess}"
                         if (g["machine"], g["source"], g["subject"]) in red_before else
                         f"baseline, red on its first run in the week (a weekly job), no fault on it: {guess}")
                cls = "baseline"
            else:
                cause = f"unexplained (no fault on this machine): {guess}"
                cls = "unexplained"
            g["cause"], g["class"] = cause, cls
            g["recurring"] = len(g["nights"]) > 1
            g["still_red_at_end"] = max(self.night_of(dt.datetime.fromisoformat(e["ts"]))
                                        for e in self.events if (e["machine"], e["subject"]) ==
                                        (g["machine"], g["subject"])) >= self.o.days
            g.pop("text", None)
            breaks.append(g)
        breaks.sort(key=lambda b: (b["first_night"], b["first_ts"], b["machine"], b["subject"]))

        faults = []
        for s in self.faults.specs:
            mine = [b for b in breaks if b["class"] == "fault" and s["id"] in b["faults"]]
            related = [c for c in calls if c.get("machine") == s["target"] and s.get("mode")
                       and c.get("mode") == s.get("mode")]
            ran = sum(1 for c in related for r in c.get("ran") or [])
            refused = sum(1 for c in related for r in c.get("refused") or [])
            detected = bool(mine)
            misbehaves = s.get("mode") in ("stash", "autostash", "reset", "hand_edit")
            if s["kind"] == "pending_work":
                detected, outcome = None, "setup"
            elif s["status"] == "pending":
                detected, outcome = None, "not reached: after the end of the run"
            elif str(s["status"]).startswith("skipped"):
                outcome = s["status"]
            elif s.get("mode") and not related:
                outcome = "not reached: the job made no model call while the fault was set"
            elif misbehaves and refused and not ran:
                outcome = "prevented: the guard refused every misbehaving command"
            elif detected:
                outcome = "detected"
            else:
                outcome = "NOT DETECTED"
            faults.append({"id": s["id"], "kind": s["kind"], "machine": s["target"], "desc": s["desc"],
                           "expect": s.get("expect", ""), "status": s["status"],
                           "start": s["start"].isoformat(), "end": s["end"].isoformat() if s["end"] else None,
                           "detected": detected, "outcome": outcome, "breaks": [f"{b['machine']}:{b['subject']}" for b in mine][:12],
                           "stand_in_calls": len(related), "misbehaviour_ran": ran,
                           "misbehaviour_refused": refused,
                           "refusals": [r["by"][:160] for c in related for r in c.get("refused") or []][:3],
                           "applied": s.get("applied") or []})
        report = {"generated": dt.datetime.now(UTC).isoformat(), "wall_seconds": round(wall),
                  "days": self.o.days, "start": self.o.start.isoformat(),
                  "machines": {m.name: {"kind": m.kind, "jobs": len(self.fleet.jobs_for(m))}
                               for m in self.fleet.machines.values()},
                  "job_runs": self.runs, "min_interval_s": self.o.min_interval_s,
                  "daemons_not_simulated": sorted(self.daemons),
                  "not_modelled": self.not_modelled,
                  "schedules_not_understood": sorted(self.unknown),
                  "notes": self.fleet.notes, "hardcoded_home": self.fleet.hardcoded_home,
                  "sandbox_env": SANDBOX_ENV,
                  "checkpoints": self.checkpoints, "breaks": breaks, "faults": faults,
                  "promise_evals": self.eval_results}
        write_outputs(report, self.o.out)
        return report


# ═════════════════════════════════════════════════════════════════════════════
# Re-running: one folder per run, a comparable summary, and a compare step
# ═════════════════════════════════════════════════════════════════════════════

#: Where `docker` puts a run when --out is not given. One folder per run, so
#: the previous run is always there to compare with.
RUNS_ROOT = Path.home() / ".datacore" / "state" / "fleet-sim"
CLASSES = ("fault", "unexplained", "baseline")


def break_key(b: dict) -> str:
    """What makes two runs' breaks "the same break": machine, source, subject
    and the first failing check with the sandbox's scratch path and every
    number taken out (line numbers, counts and temp names change run to run)."""
    check = b.get("first_check", "").split(" (carried:")[0]
    check = re.sub(r"/\S*?fleet-sim-[^/\s]+", "<sandbox>", check)
    check = re.sub(r"\d+", "#", check)[:120]
    return " | ".join((b.get("machine", ""), b.get("source", ""), b.get("subject", ""), check))


def summarize(report: dict) -> dict:
    """The small, stable, machine-readable view of a run that `compare` reads."""
    counts = {c: sum(1 for b in report["breaks"] if b.get("class") == c) for c in ("baseline", "fault",
                                                                                   "unexplained")}
    counts["total"] = len(report["breaks"])
    return {"generated": report.get("generated"), "start": report.get("start"), "days": report.get("days"),
            "job_runs": report.get("job_runs"), "wall_seconds": report.get("wall_seconds"),
            "counts": counts,
            "faults": {f["id"]: f.get("outcome") for f in report.get("faults") or []},
            "breaks": sorted(({"key": break_key(b), "class": b.get("class"), "machine": b.get("machine"),
                               "source": b.get("source"), "subject": b.get("subject"),
                               "first_check": b.get("first_check", "")[:200], "nights": b.get("nights")}
                              for b in report["breaks"]), key=lambda x: x["key"])}


def write_outputs(report: dict, out: Path) -> None:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    (out / "report.md").write_text(render_md(report))
    (out / "summary.json").write_text(json.dumps(summarize(report), indent=1, default=str))


def load_summary(run: Path) -> dict:
    run = Path(run)
    if (run / "summary.json").is_file():
        return json.loads((run / "summary.json").read_text())
    return summarize(json.loads((run / "report.json").read_text()))


def default_out(root: Path, days: int, today: dt.date | None = None) -> Path:
    base = f"{(today or dt.date.today()).isoformat()}-{days}d"
    out, n = Path(root) / base, 1
    while out.exists():
        n += 1
        out = Path(root) / f"{base}-{n}"
    return out


def _is_run(d: Path) -> bool:
    return d.is_dir() and (d / "report.json").is_file()


def previous_run(root: Path, current: Path) -> Path | None:
    """The newest finished run in `root` before `current`, preferring one of
    the same length (a 2-day run is not compared with a 7-day one if a 7-day
    one exists)."""
    current = Path(current).resolve()

    def order(d: Path) -> tuple:
        # Finish time, then name: two runs finished within the file system's
        # time resolution (seen in the container) still have one order.
        return ((d / "report.json").stat().st_mtime_ns, d.name)
    cur_k = order(current) if _is_run(current) else (float("inf"), "")
    runs = [d for d in Path(root).iterdir() if _is_run(d) and d.resolve() != current
            and order(d) < cur_k] if Path(root).is_dir() else []
    if not runs:
        return None
    try:
        days = load_summary(current).get("days") if _is_run(current) else None
        same = [d for d in runs if load_summary(d).get("days") == days]
    except (OSError, ValueError, KeyError):
        same = []
    return max(same or runs, key=order)


def compare_runs(prev: Path, cur: Path) -> dict:
    a, b = load_summary(prev), load_summary(cur)
    ka, kb = {x["key"]: x for x in a["breaks"]}, {x["key"]: x for x in b["breaks"]}
    fa, fb = a.get("faults") or {}, b.get("faults") or {}
    return {"prev": str(prev), "cur": str(cur), "prev_counts": a["counts"], "cur_counts": b["counts"],
            "new": [kb[k] for k in sorted(kb) if k not in ka],
            "fixed": [ka[k] for k in sorted(ka) if k not in kb],
            "still": [kb[k] for k in sorted(kb) if k in ka],
            "faults_changed": {i: [fa.get(i), fb.get(i)] for i in sorted(set(fa) | set(fb))
                               if fa.get(i) != fb.get(i)}}


def render_compare(d: dict) -> str:
    pc, cc = d["prev_counts"], d["cur_counts"]
    L = [f"# Fleet week simulation: compared with the previous run", "",
         f"Previous: `{d['prev']}`  ", f"This run: `{d['cur']}`", "",
         "| Class | Previous | This run |", "|---|---|---|"]
    L += [f"| {c} | {pc.get(c, 0)} | {cc.get(c, 0)} |" for c in (*CLASSES, "total")]
    for title, rows in (("New breaks", d["new"]), ("Fixed (red before, not now)", d["fixed"])):
        L += ["", f"## {title} ({len(rows)})", ""]
        L += [f"- [{r['class']}] {r['machine']} / {r['subject']} ({r['source']}): "
              f"{r['first_check'][:140]}" for r in rows] or ["- none"]
    L += ["", f"## Still red ({len(d['still'])})", ""]
    by = {c: sum(1 for r in d["still"] if r["class"] == c) for c in CLASSES}
    L += [f"- {', '.join(f'{v} {k}' for k, v in by.items())}"]
    L += [f"- [{r['class']}] {r['machine']} / {r['subject']}" for r in d["still"] if r["class"] != "baseline"]
    if d["faults_changed"]:
        L += ["", "## Fault outcomes that changed", ""]
        L += [f"- {i}: {a} -> {b}" for i, (a, b) in d["faults_changed"].items()]
    return "\n".join(L) + "\n"


def render_md(r: dict) -> str:
    L = [f"# Fleet week simulation, {r['start']} + {r['days']} nights",
         "",
         f"{len(r['machines'])} machines, {r['job_runs']} job runs, {r['wall_seconds']}s wall time. "
         f"Jobs firing more often than every {r['min_interval_s'] // 60} min ran once per "
         f"{r['min_interval_s'] // 60} min.", ""]
    L += ["## Injected faults", "", "| Fault | Machine | What | Outcome | Misbehaviour ran / refused | Red checks |",
          "|---|---|---|---|---|---|"]
    for f in r["faults"]:
        L.append(f"| {f['id']} {f['kind']} | {f['machine']} | {f['desc'][:90]} | {f['outcome']} | "
                 f"{f['misbehaviour_ran']} / {f['misbehaviour_refused']} | {', '.join(f['breaks'][:4])} |")
    for cls, title in (("fault", "Breaks caused by an injected fault"),
                       ("unexplained", "Unexplained breaks (no fault on that machine)"),
                       ("baseline", "Baseline breaks (red before any fault was injected)")):
        rows = [b for b in r["breaks"] if b["class"] == cls]
        L += ["", f"## {title} ({len(rows)})", "",
              "| Night | First seen | Machine | Job / subject | Source | First failing check | Cause guess | Nights |",
              "|---|---|---|---|---|---|---|---|"]
        for b in rows:
            chk = b["first_check"].replace("|", "\\|")[:160]
            L.append(f"| {b['first_night']} | {b['first_ts'][5:16]} | {b['machine']} | {b['subject']} | "
                     f"{b['source']} | {chk} | {b['cause'].replace('|', '/')[:140]} | "
                     f"{','.join(map(str, b['nights']))} |")
    L += ["", "## Checkpoints", "", "| Night | At | Red | Not checked (offline) | Evals | s |", "|---|---|---|---|---|---|"]
    for c in r["checkpoints"]:
        L.append(f"| {c['night']} | {c['ts'][5:16]} | {c['found']} | {', '.join(c['offline_not_checked'])} | "
                 f"{'ran ' + json.dumps(c['eval_counts']) if c['evals_ran'] else '-'} | {c['secs']} |")
    if r["daemons_not_simulated"] or r["schedules_not_understood"] or r.get("not_modelled"):
        L += ["", "## Not simulated", ""]
        L += [f"- daemon (never exits, so not run): {d}" for d in r["daemons_not_simulated"]]
        L += [f"- not modelled: {k}: {v}" for k, v in (r.get("not_modelled") or {}).items()]
        L += [f"- schedule not understood: {d}" for d in r["schedules_not_understood"]]
    if r["notes"]:
        L += ["", "## Notes", ""] + [f"- {n}" for n in r["notes"]]
    return "\n".join(L) + "\n"


def run_week(opts: Options) -> dict:
    return Week(opts).run()


# ═════════════════════════════════════════════════════════════════════════════
# Command line
# ═════════════════════════════════════════════════════════════════════════════

def _docker(args) -> int:
    src = LIB.parents[1]
    base = Path(args.workdir or tempfile.mkdtemp(prefix="fleet-sim-")).resolve()
    seed = base / "seed"
    if args.out:
        out = Path(args.out).resolve()
    elif args.selftest:
        out = base / "out"
    else:
        out = default_out(RUNS_ROOT, args.days)
    out.mkdir(parents=True, exist_ok=True)
    print(f"[fleet-sim] seed -> {seed}", flush=True)
    prepare(src, seed)
    if not args.no_build:
        subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(SIMDIR)], check=True)
    inner = ["python3", "/seed/core-src/.datacore/lib/fleet_week_sim.py"]
    if args.selftest:
        inner = ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 "/seed/core-src/.datacore/lib/tests/test_fleet_week_sim.py"]
    else:
        inner += ["run", "--seed", "/seed", "--out", "/out", "--days", str(args.days),
                  "--evals", args.evals, "--min-interval", str(args.min_interval)]
        if args.no_faults:
            inner.append("--no-faults")
        elif args.faults:
            shutil.copy2(args.faults, out / "faults.json")   # /out is the only writable mount
            inner += ["--faults", "/out/faults.json"]
    cmd = ["docker", "run", "--rm", "--network", "none", "-e", "FLEET_SIM_SEED=/seed",
           "-w", "/seed/core-src/.datacore/lib",
           "-v", f"{seed}:/seed:ro", "-v", f"{out}:/out", IMAGE, *inner]
    print("[fleet-sim] " + " ".join(shlex.quote(c) for c in cmd), flush=True)
    rc = subprocess.run(cmd).returncode
    print(f"[fleet-sim] output in {out}", flush=True)
    if not args.selftest and _is_run(out):
        prev = previous_run(out.parent, out)
        if prev is None:
            print("[fleet-sim] no previous run in the same folder to compare with", flush=True)
        else:
            text = render_compare(compare_runs(prev, out))
            (out / "compare.md").write_text(text)
            print(text, flush=True)
            print(f"[fleet-sim] comparison written to {out / 'compare.md'}", flush=True)
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="write the seed (host side)")
    p.add_argument("--seed", required=True)
    p = sub.add_parser("run", help="run the week (inside Linux with libfaketime)")
    p.add_argument("--seed", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--start", default="2026-10-01")
    p.add_argument("--evals", choices=("off", "once", "changed", "nightly"), default="changed")
    p.add_argument("--min-interval", type=int, default=3600, help="seconds; see fires()")
    p.add_argument("--no-faults", action="store_true")
    p.add_argument("--faults", help="JSON file with a fault list instead of the default week")
    p.add_argument("--workdir")
    p = sub.add_parser("docker", help="prepare, build the image and run the week in it (host side)")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--out")
    p.add_argument("--workdir")
    p.add_argument("--evals", choices=("off", "once", "changed", "nightly"), default="changed")
    p.add_argument("--min-interval", type=int, default=3600)
    p.add_argument("--no-faults", action="store_true")
    p.add_argument("--no-build", action="store_true")
    p.add_argument("--faults", help="JSON file with a fault list instead of the default week")
    p.add_argument("--selftest", action="store_true", help="run the harness's own tests in the container")
    p = sub.add_parser("compare", help="compare a run's breaks with an earlier run's")
    p.add_argument("run", help="the run folder (holds report.json)")
    p.add_argument("--prev", help="the earlier run folder; default: the newest earlier run next to it")
    a = ap.parse_args(argv)
    if a.cmd == "compare":
        cur = Path(a.run).resolve()
        prev = Path(a.prev).resolve() if a.prev else previous_run(cur.parent, cur)
        if prev is None:
            print(f"[fleet-sim] no earlier run next to {cur}", file=sys.stderr)
            return 1
        text = render_compare(compare_runs(prev, cur))
        (cur / "compare.md").write_text(text)
        print(text)
        return 0
    if a.cmd == "prepare":
        print(prepare(LIB.parents[1], Path(a.seed).resolve()))
        return 0
    if a.cmd == "docker":
        return _docker(a)
    faults = [] if a.no_faults else (json.loads(Path(a.faults).read_text()) if a.faults else None)
    report = run_week(Options(seed=Path(a.seed), out=Path(a.out), days=a.days,
                              start=dt.date.fromisoformat(a.start), faults=faults, evals=a.evals,
                              min_interval_s=a.min_interval, workdir=Path(a.workdir) if a.workdir else None))
    missed = [f["id"] for f in report["faults"] if f["detected"] is False]
    print(f"[fleet-sim] {len(report['breaks'])} break(s); faults not detected: {missed or 'none'}; "
          f"{report['wall_seconds']}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
