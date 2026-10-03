---
name: fleet-sim
description: Run the fleet week simulator (a simulated week of every machine's real schedule, with injected faults) and compare its breaks with the previous run
recall:
  # DIP-0029 default — engrams scoped to this command + tag-matched.
  scopes:
    - command:fleet-sim
  tags:
    - fleet-sim
---

# /fleet-sim

Run a simulated week of the whole fleet's real schedule in a sealed Linux
container on the Mac, and list what breaks — then say what changed since the
last run. Issue datacore-one/datacore #222.

## Usage

```
/fleet-sim            # 7 simulated nights (about an hour)
/fleet-sim 2          # 2 nights (quick check after a fix)
/fleet-sim selftest   # the simulator's own tests, in the container (about ten minutes)
/fleet-sim harsh      # 7 nights with the harsh faults on top (F13-F25), compressed to 2-hour steps
```

`harsh` adds the faults of `HARSH_FAULTS` in fleet_week_sim.py to F1-F12, several
at once: network throttling and loss (real `tc netem` on the container's
loopback plus the stand-ins), partitions between pairs of machines, a machine
down for hours mid-job, repeated reboots, out of memory mid-task (the process,
and the whole run), a full disk, clock skew, GitHub unreachable, a rotated
credential on one machine, a second writer in the overnight host's checkout,
a slow and a rate-limited model. Run it as
`python3 .datacore/lib/fleet_week_sim.py docker --days 7 --harsh --min-interval 7200`
(about two hours). The report opens with what the owner would have had each
night: was the delegated task completed, did the briefing and its audio go out.

Not scheduled. The owner decides a cadence later; run it by hand.

## Steps

1. **Pre-flight.** On the Mac only (it needs Docker). Check `docker info` answers.
   If Docker is not running, say so and stop — do not try another machine.
   The simulator reads committed files (git HEAD) plus five gitignored config
   files; say if the working tree has uncommitted product changes, because
   those are NOT in the run.

2. **Run.** From the install root, in the background (it takes 50–75 min for 7 nights):

   ```bash
   python3 .datacore/lib/fleet_week_sim.py docker --days N
   ```

   `N` is the argument, default 7. For `selftest`:
   `python3 .datacore/lib/fleet_week_sim.py docker --selftest` (no report, no compare).

3. **Where the output lands.** Without `--out`, every run gets its own folder:
   `~/.datacore/state/fleet-sim/<YYYY-MM-DD>-<N>d/` (a `-2`, `-3` suffix for a
   second run the same day). It holds:
   - `report.md` / `report.json` — every break, night by night, with machine,
     job, first failing check, a cause guess and its class (fault / unexplained /
     baseline), and each injected fault's outcome
   - `summary.json` — the small stable view used for comparing runs
   - `compare.md` — this run against the previous one (step 4)
   - `calls.jsonl`, `runs.jsonl`, `job-logs.tar.gz` — stand-in model calls, per-run timings, every job's log

4. **Compare.** The `docker` run compares itself with the newest earlier run in
   the same folder (preferring one with the same number of nights) and writes
   `compare.md`: new breaks, fixed breaks, still red, and fault outcomes that
   changed. To compare two chosen runs:

   ```bash
   python3 .datacore/lib/fleet_week_sim.py compare <run-folder> [--prev <earlier-run-folder>]
   ```

5. **Report back** in plain language: counts per class against the previous
   run, every NEW break and every fault whose outcome changed, and each
   unexplained break. Name the run folder. A break that is new is not
   necessarily a regression in the product — the harness may have changed too;
   say which commits landed between the two runs (`git log` over the harness
   files and the product) if a new break needs explaining.

## Boundaries

- Never edits a promise eval, never weakens a check, never touches the real
  fleet: the container runs with `--network none`. Its two added capabilities
  (NET_ADMIN for netem, SYS_ADMIN for the full-disk mounts) act inside it only.
- Reads no credential (.env, secrets directory).
- Do not schedule it (cron, launchd, nightshift) without the owner deciding the cadence.
