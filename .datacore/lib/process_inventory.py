#!/usr/bin/env python3
"""process_inventory — every scheduled process in the fleet, and where two of them overlap.

Owner, 2026-10-30: "We have recurring problems with task delegation and other
processes breaking. We just identified that two copies of the same process were
run by Miles and Winston." The morning delegation review ran on Winston (its
manifest job) and on Miles (a dayshift timer declared only in a host-local
manifest); both wrote one receipt in 2-datacore and the gate read the merge as
'hold'. Nothing listed both, so nothing could see it.

This lists, from the code and the live schedules (never from memory):
  * every job contract in .datacore/lib/jobs/manifest.yaml: trigger, machine,
    actor, command, the files it writes (its artifacts);
  * every command actually scheduled on each machine (crontab, systemd timers,
    pod crontab), read through fleet_reach -- read-only;
and flags overlaps:
  * same-entrypoint: one script scheduled on two or more machines;
  * same-output: one shared-repo file (under ~/Data) written by two or more jobs;
  * no-contract: a script scheduled on a machine with no job contract anywhere, so nothing
    checks that it ran (contracts in host-local manifests and venture cadences are listed
    separately, as not-in-shared-manifest).

    process_inventory.py [--out PATH] [--no-live]
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))
ROOT = LIB.parent.parent
MANIFEST = LIB / "jobs" / "manifest.yaml"
OUT = ROOT / "2-datacore" / "1-tracks" / "dev" / "architecture" / "processes.yaml"

#: Script-like tokens in a command line: the thing that identifies a process.
_SCRIPT = re.compile(r"([A-Za-z0-9_.-]+\.(?:py|sh))\b|-m\s+([A-Za-z_][A-Za-z0-9_.]*)")
_TAG = re.compile(r"datacore-job:([A-Za-z0-9_.-]+)")
#: Processes every machine runs for ITSELF, by design (its own ledger cycle, its own
#: checker, its own scoreboard, its own sync). Several copies are expected, not overlap.
PER_MACHINE = {"ledger_phase1_cycle.sh", "ledger_transport.py", "ledger_claim.py", "ledger-claim-pull.sh",
               "job_verify.py", "job_verify_notify.sh", "promise_nightly.py", "sync_traces.sh",
               "cross_model_audit.py", "git_fleet_sync.py", "atomic_out.sh", "timer.py", "plur_store_sync.py",
               "cadence_run.py", "cos_alert.sh"}
#: The same idea for job tags in host crontabs (datacore-job:<name>).
PER_MACHINE_TAGS = {"phase1-cycle", "runner-refresh", "sync-traces", "job-verify", "ledger-claim", "bot-alive",
                    "gate-check"}
#: Wrappers that run some other process; the process is their argument, not them.
_WRAPPERS = {"job_verify.py", "audit_trio_run.sh", "self_update.sh", "run_logged.sh", "lock.sh"}


def entrypoints(cmd: str) -> list[str]:
    """Script names a command runs, wrappers kept only when nothing else is named."""
    found = [a or b for a, b in _SCRIPT.findall(cmd or "")]
    real = [f for f in found if f not in _WRAPPERS]
    return sorted(set(real or found))


def _trigger(job: dict) -> str:
    for k in ("schedule", "every", "trigger", "after"):
        if job.get(k):
            return f"{k}: {job[k]}"
    return "unscheduled"


def _actor(machine: str) -> str | None:
    try:
        from jobs.autofix import actor_of
        return actor_of(machine)
    except Exception:  # noqa: BLE001 -- the roster may be absent in a test checkout
        return None


def manifest_processes(manifest: Path = MANIFEST) -> list[dict]:
    jobs = (yaml.safe_load(manifest.read_text()) or {}).get("jobs") or []
    out = []
    for j in jobs:
        out.append({
            "name": j["name"],
            "machine": j.get("machine"),
            "actor": _actor(j.get("machine") or ""),
            "trigger": _trigger(j),
            "entrypoints": entrypoints(j.get("cmd", "")),
            "writes": sorted({a["path"] for a in j.get("artifacts") or [] if a.get("path")}),
            "checked_by": f"job_verify on {j.get('machine')}",
        })
    return out


def live_schedules(machines: list[tuple[str, bool]]) -> dict[str, list[str]]:
    """{place label: [entrypoint, ...]} for each machine's live schedule; read-only."""
    import fleet_reach as fr
    out: dict[str, list[str]] = {}
    for name, host in machines:
        label = f"{name}{' host' if host else ''}"
        try:
            pl = fr.place(name, host=host)
            r = fr.run_at(pl, fr.schedule_listing(pl), timeout=60)
        except Exception as exc:  # noqa: BLE001
            out[label] = [f"(unreadable: {type(exc).__name__})"]
            continue
        ids: set[str] = set()
        for line in r.stdout.splitlines():
            if line.strip().startswith("#") or not line.strip():
                continue
            ids.add(identity(line))
        out[label] = sorted(i for i in ids if i)
    return out


def identity(line: str) -> str:
    """What one scheduled line runs: its manifest job tag, else its script plus first argument."""
    tag = _TAG.search(line)
    if tag:
        return "job:" + tag.group(1)
    eps = entrypoints(line)
    if not eps:
        return ""
    ep = eps[0]
    after = line.split(ep, 1)[1].split(">")[0].split()
    arg = next((a for a in after if not a.startswith(("-", "$", "~", "/", "2", "&", "|"))), "")
    return f"{ep} {arg}".strip()


def overlaps(procs: list[dict], live: dict[str, list[str]]) -> list[dict]:
    flags = []
    # same output: a shared-repo file written by two or more jobs
    by_file = defaultdict(list)
    for p in procs:
        for w in p["writes"]:
            if w.startswith("~/Data/"):
                by_file[w].append(p)
    for f, ps in sorted(by_file.items()):
        if len(ps) > 1:
            flags.append({"kind": "same-output", "what": f,
                          "by": [f"{p['name']} ({p['machine']}/{p['actor']})" for p in ps]})
    # the same scheduled thing on two or more machines (per-machine-by-design processes excepted)
    names = {p["name"]: p for p in procs}
    where = defaultdict(set)
    for label, ids in live.items():
        for i in ids:
            if i.startswith("("):
                continue
            ep = i.split()[0] if not i.startswith("job:") else (names.get(i[4:], {}).get("entrypoints") or [i])[0]
            if ep in PER_MACHINE or i[4:] in PER_MACHINE_TAGS or i.endswith("job-verify-hourly"):
                continue
            where[i].add(label)
    for i, labels in sorted(where.items()):
        if len(labels) > 1:
            flags.append({"kind": "same-process", "what": i, "by": sorted(labels)})
    # scheduled on a machine with no contract for it there
    declared = defaultdict(set)
    for p in procs:
        declared[p["machine"]].update(p["entrypoints"])
        declared[p["machine"]].add("job:" + p["name"])
    alias = {"box host": "box"}
    for label, ids in sorted(live.items()):
        m = alias.get(label, label)
        mine = declared.get(m, set()) | (declared.get("dayshift", set()) if m == "nightshift" else set())
        for i in ids:
            if i.startswith("("):
                continue
            if i.startswith("job:"):
                # venture cadences and host-local contracts (manifest.local.yaml) are declared
                # outside the shared manifest; listed so the gap is visible, not as a fault
                if i not in mine and i[4:] not in PER_MACHINE_TAGS and "job-verify" not in i:
                    kind = "cadence-not-in-shared-manifest" if i.startswith("job:cadence-") else "contract-not-in-shared-manifest"
                    flags.append({"kind": kind, "what": i[4:], "by": [label]})
                continue
            if i.split()[0] in PER_MACHINE or i.split()[0] in _WRAPPERS:
                continue
            if i.split()[0] not in mine:
                flags.append({"kind": "no-contract", "what": i, "by": [label]})
    return flags


def build(live: bool = True) -> dict:
    procs = manifest_processes()
    sched = live_schedules([("box", False), ("box", True), ("nightshift", False), ("hermes", False),
                            ("plur-claw", False)]) if live else {}
    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "jobs/manifest.yaml + live schedules via fleet_reach (read-only); built by process_inventory.py",
        "processes": procs,
        "live_schedules": sched,
        "overlaps": overlaps(procs, sched),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-live", action="store_true", help="manifest only; skip reading the machines")
    a = ap.parse_args(argv)
    data = build(live=not a.no_live)
    Path(a.out).write_text(yaml.safe_dump(data, sort_keys=False, width=120, allow_unicode=True))
    kinds = defaultdict(int)
    for f in data["overlaps"]:
        kinds[f["kind"]] += 1
    print(f"process-inventory: {len(data['processes'])} processes; overlaps: "
          + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())) + f" -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
