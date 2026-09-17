#!/usr/bin/env python3
"""verifier_replay -- re-judge every past mac staleness alert with today's verifier.

A stale alert records the artifact's age at the moment it fired, so its write time is
recoverable: mtime = run time - reported age. Replaying each through jobs.awake against
the REAL `pmset -g log` answers, over every night the power log still holds, "would this
alert still fire?" -- in seconds, instead of waiting for nights to happen.

Alerts that still fire are the interesting output: each is either a real outage while
the machine was awake, or a hole in the sleep accounting. Non-stale failures are tallied
separately, because they are not a sleep question and each needs its own cause.

  python3 .datacore/lib/verifier_replay.py        # REPLAY_LOG / REPLAY_UNTIL to override
"""
import re, sys, collections
from datetime import datetime, timezone
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from jobs import awake

import os
LOG = os.environ.get("REPLAY_LOG") or os.path.expanduser("~/.datacore/state/job_verify.log")
#: Runs from this point on already report AWAKE age, so their numbers cannot be
#: re-judged. Default: when the sleep-aware check first took effect in the runner.
DEPLOY = datetime.fromisoformat(os.environ.get("REPLAY_UNTIL") or "2026-09-17T09:10:00+00:00").timestamp()
pm = awake._sleep_log()
stamps = [datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S %z").timestamp()
          for m in (awake._LOG_LINE.match(l.strip()) for l in pm.splitlines()) if m]
LOG_START = min(stamps)

STALE = re.compile(r"^\s+- (\S+): stale \(age ([0-9.]+)h exceeds max_age_hours=([0-9.]+)\)")
JOB = re.compile(r"^job '([^']+)' FAILED:")
runs = []            # (ts, [(job, kind, detail)])
cur_ts, cur_job = None, None
for line in open(LOG, encoding="utf-8", errors="replace"):
    if line.startswith("=== ") and line.strip().endswith("==="):
        try:
            cur_ts = datetime.fromisoformat(line.strip("= \n").replace("Z", "+00:00")).timestamp()
            runs.append((cur_ts, []))
        except ValueError:
            cur_ts = None
        continue
    if cur_ts is None:
        continue
    m = JOB.match(line)
    if m:
        cur_job = m.group(1); continue
    if cur_job and line.startswith("  - "):
        s = STALE.match(line)
        kind = "stale" if s else re.sub(r"[0-9.]+", "N", line.split(":", 1)[-1].strip())[:70]
        runs[-1][1].append((cur_job, kind, s.groups() if s else None))

window = [(t, f) for t, f in runs if LOG_START <= t < DEPLOY]
print(f"verifier runs in the replay window ({datetime.fromtimestamp(LOG_START, timezone.utc):%m-%d %H:%M}Z .. deploy): {len(window)}")
print(f"  runs that failed: {sum(1 for _, f in window if f)}  |  total failure lines: {sum(len(f) for _, f in window)}")

still, suppressed, before_log = [], 0, 0
per_job = collections.Counter(); per_job_still = collections.Counter()
other = collections.Counter()
for t, fails in window:
    for job, kind, g in fails:
        if kind != "stale":
            other[(job, kind)] += 1
            continue
        path, wall_h, max_h = g[0], float(g[1]), float(g[2])
        mtime = t - wall_h * 3600
        if mtime < LOG_START:
            before_log += 1; continue
        age = awake.awake_age(mtime, "mac", now=t, log=pm)
        per_job[job] += 1
        if age > max_h * 3600:
            still.append((t, job, wall_h, age / 3600, max_h))
            per_job_still[job] += 1
        else:
            suppressed += 1

print(f"\nSTALE alerts replayable: {suppressed + len(still)}  (+{before_log} whose artifact predates the sleep log)")
print(f"  suppressed by the fix (machine was asleep): {suppressed}")
print(f"  STILL FIRE under the fix:                   {len(still)}")
for job in sorted(per_job):
    print(f"    {job:26} {per_job[job]:4} stale alerts -> {per_job_still[job]:3} still fire")
print("\nstill-firing detail (awake age exceeds bound even after subtracting sleep):")
for t, job, wall, aw, mx in still[:25]:
    print(f"  {datetime.fromtimestamp(t, timezone.utc):%m-%d %H:%MZ}  {job:24} wall {wall:5.2f}h  awake {aw:5.2f}h  max {mx:g}h")
print("\nNON-stale failures in the window (not a sleep question -- each needs its own cause):")
for (job, kind), n in other.most_common(30):
    print(f"  {n:4}  {job:24} {kind}")
