#!/usr/bin/env python3
"""Why contracts went red, from the verifier's own log.

    job_verify_history.py [--log ~/.datacore/state/job_verify.log] [--since YYYY-MM-DD]
                          [--manifest .../jobs/manifest.yaml] [--machine NAME] [--json]

Every failure detail line job_verify ever printed falls into one of a few
CLASSES, and the class is what says what to do about it:

  stale      the producer did not run, or did not write, within max_age
             -> a scheduler, a lock, a crash before writing, a sleeping host
  missing    the artifact is not there at all       -> never ran here, or wrong path
  crash      a no_crash marker in the output         -> the producer died noisily
  empty      the artifact is an empty file           -> a truncating write, or a crash
  regex      fresh, present, and the TEXT did not match the contract
             -> either the producer's output changed shape (a FALSE red), or the
                producer is honestly reporting that the world is unhealthy
                (a TRUE red that needs a person, not a repair)
  repo       a required repo is behind its upstream  -> sync, not the producer
  verifier   job_verify itself raised                -> our bug

The second half reads the manifest and says, per contract, whether it asserts
that the PRODUCER RAN (exists/nonempty/no_crash/"OK ..." lines) or that the
WORLD IS HEALTHY ("0 overdue", "0 repair(s)", "SOUND", "0 FAIL"). Those two
kinds go red for different reasons and should not be treated alike.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

CLASSES = (
    ("stale", re.compile(r"stale \(age")),
    ("missing", re.compile(r"does not exist")),
    ("crash", re.compile(r"the run left|no_crash check failed\) --")),
    ("empty", re.compile(r"empty file")),
    ("repo", re.compile(r"behind|not synced|unpushed")),
    ("verifier", re.compile(r"unexpected exception|Traceback")),
    ("regex", re.compile(r"did not match|does not match|missing keys|invalid JSON")),
)

WORLD_STATE = re.compile(
    r"\b0 (cadence|repair|unexplained|error|FAIL|with unpublished|suite|drift|space|gap|issue|failed)"
    r"|SOUND|converged\": true|0 past due|need a person|0 could-not-tell|held\b", re.I)


def classify(detail: str) -> str:
    for name, rx in CLASSES:
        if rx.search(detail):
            return name
    return "other"


def parse(log: Path, since: str | None):
    """[(ts, job, class, detail)], plus decisions per block."""
    rows, decisions = [], []
    ts, job = None, None
    for line in log.read_text(errors="replace").splitlines():
        m = re.match(r"^=== (\S+) ===", line)
        if m:
            ts = m.group(1); job = None
            continue
        if ts is None or (since and ts[:10] < since):
            continue
        m = re.match(r"^job '([^']+)' FAILED:", line)
        if m:
            job = m.group(1); continue
        if job and line.startswith("  - "):
            rows.append((ts, job, classify(line), line[4:].strip()))
            continue
        for kind in ("alert withheld:", "alert:", "delegated repair of", "could NOT delegate",
                     "alert suppressed:", "relay: nothing", "sent 1 message"):
            if line.startswith(kind):
                decisions.append((ts, kind.rstrip(":")))
    return rows, decisions


def from_ledger(space: Path, since: str | None):
    """[(ts, actor, job, ok, [details])] from every writer's job.verify events."""
    import datetime as dt
    rows = []
    for f in sorted((space / ".datacore" / "events").glob("*.jsonl")):
        for line in f.read_text(errors="replace").splitlines():
            if '"job.verify"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            p = e.get("payload") or {}
            if p.get("metric") != "job.verify":
                continue
            try:
                ms = int(str(e.get("hlc", "0")).split(".")[0])
                ts = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (ValueError, OSError):
                continue
            if since and ts[:10] < since:
                continue
            rows.append((ts, e.get("actor", "?"), p.get("job"), bool(p.get("ok")), list(p.get("failures") or [])))
    return rows


def report_ledger(rows, kinds):
    by_actor: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"runs": 0, "fails": 0, "classes": defaultdict(int), "first_fail": None, "last_fail": None, "last": ""}))
    for ts, actor, job, ok, fails in rows:
        p = by_actor[actor][job]
        p["runs"] += 1
        if not ok:
            p["fails"] += 1
            for d in fails:
                p["classes"][classify(d)] += 1
            p["first_fail"] = min(p["first_fail"] or ts, ts); p["last_fail"] = max(p["last_fail"] or ts, ts); p["last"] = fails[0] if fails else ""
    for actor, jobs in by_actor.items():
        runs = sum(j["runs"] for j in jobs.values()); fails = sum(j["fails"] for j in jobs.values())
        days = len({ts[:10] for ts, a, *_ in rows if a == actor})
        print(f"\n== {actor}: {runs} verifications over {days} day(s), {fails} failed ({100*fails/max(runs,1):.0f}%)")
        print(f"   {'job':32s} {'kind':13s} {'fail/runs':>9s}  classes                    first_fail..last_fail")
        for job, p in sorted(jobs.items(), key=lambda kv: -kv[1]["fails"]):
            if not p["fails"]:
                continue
            cls = ",".join(f"{c}:{n}" for c, n in sorted(p["classes"].items(), key=lambda kv: -kv[1]))
            print(f"   {str(job)[:32]:32s} {kinds.get(job, '?'):13s} {p['fails']:4d}/{p['runs']:<4d}  {cls[:26]:26s} {p['first_fail'][:10]}..{p['last_fail'][:10]}")
        green = [j for j, p in jobs.items() if not p["fails"]]
        print(f"   never failed in the window: {len(green)} job(s)")


def contract_kind(job: dict) -> str:
    for a in job.get("artifacts") or []:
        check, arg = a.get("check"), str(a.get("arg") or "")
        if check in ("last_line_regex", "regex") and WORLD_STATE.search(arg):
            return "world-state"
    return "producer-ran"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", default=os.path.expanduser("~/.datacore/state/job_verify.log"))
    ap.add_argument("--since")
    ap.add_argument("--manifest", default=str(Path(__file__).resolve().parent / "jobs" / "manifest.yaml"))
    ap.add_argument("--machine")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--ledger", type=Path, metavar="SPACE",
                    help="read job.verify events from this space's ledger instead of a log (all hosts at once)")
    a = ap.parse_args()

    if a.ledger:
        kinds = {}
        try:
            import yaml
            kinds = {j["name"]: contract_kind(j) for j in yaml.safe_load(Path(a.manifest).read_text())["jobs"]}
        except Exception as exc:  # noqa: BLE001
            print(f"manifest not read: {exc}", file=sys.stderr)
        rows = from_ledger(a.ledger, a.since)
        print(f"{len(rows)} job.verify event(s) in {a.ledger}" + (f" since {a.since}" if a.since else ""))
        report_ledger(rows, kinds)
        ws = sorted(j for j, k in kinds.items() if k == "world-state")
        print(f"\nworld-state contracts ({len(ws)}): " + ", ".join(ws))
        return 0

    rows, decisions = parse(Path(a.log), a.since)
    per_job: dict[str, dict] = defaultdict(lambda: {"runs": set(), "classes": defaultdict(int), "first": None, "last": None, "last_detail": ""})
    for ts, job, cls, detail in rows:
        p = per_job[job]
        p["runs"].add(ts); p["classes"][cls] += 1
        p["first"] = min(p["first"] or ts, ts); p["last"] = max(p["last"] or ts, ts); p["last_detail"] = detail
    kinds = {}
    try:
        import yaml
        for j in yaml.safe_load(Path(a.manifest).read_text())["jobs"]:
            if not a.machine or j.get("machine") == a.machine:
                kinds[j["name"]] = contract_kind(j)
    except Exception as exc:  # noqa: BLE001
        print(f"manifest not read: {exc}", file=sys.stderr)

    out = {"jobs": {}, "decisions": defaultdict(int), "class_totals": defaultdict(int), "kinds": kinds}
    for job, p in sorted(per_job.items(), key=lambda kv: -len(kv[1]["runs"])):
        out["jobs"][job] = {"failed_runs": len(p["runs"]), "classes": dict(p["classes"]), "first": p["first"],
                            "last": p["last"], "kind": kinds.get(job, "?"), "last_detail": p["last_detail"][:160]}
        for c, n in p["classes"].items():
            out["class_totals"][c] += n
    for _, kind in decisions:
        out["decisions"][kind] += 1
    if a.json:
        print(json.dumps(out, indent=1, default=dict)); return 0

    blocks = len({ts for ts, *_ in rows})
    print(f"{len(rows)} failure line(s) in {blocks} failing run(s)" + (f" since {a.since}" if a.since else ""))
    print(f"by class: " + ", ".join(f"{c}={n}" for c, n in sorted(out['class_totals'].items(), key=lambda kv: -kv[1])))
    print(f"decisions: " + ", ".join(f"{k}={n}" for k, n in sorted(out['decisions'].items(), key=lambda kv: -kv[1])))
    print()
    print(f"{'job':34s} {'kind':13s} {'runs':>4s}  classes                          first..last")
    for job, p in out["jobs"].items():
        cls = ",".join(f"{c}:{n}" for c, n in sorted(p["classes"].items(), key=lambda kv: -kv[1]))
        print(f"{job[:34]:34s} {p['kind']:13s} {p['failed_runs']:4d}  {cls[:32]:32s} {p['first'][:10]}..{p['last'][:10]}")
    print()
    print("contracts by kind: " + ", ".join(f"{k}={sum(1 for v in kinds.values() if v == k)}" for k in ("producer-ran", "world-state")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
