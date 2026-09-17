#!/usr/bin/env python3
"""laptop_night_drill -- a laptop night in seconds, through the verifier that actually runs.

WHY. The laptop-aware freshness check shipped 2026-09-16 and did nothing for a day: it
was verified from ~/Data, where the gitignored roster exists, while cron runs the
v2-runner checkout, where it does not. Then, once enabled, a `Wake Requests` line was
counted as a wake and a non-UTF-8 byte in the power log crashed every mac contract.
None of that was visible without waiting for a real night. This makes a night on demand.

HOW. Every scenario builds a scratch data root and artifacts with chosen ages, puts a
fake `pmset` first on PATH printing a chosen power log, and runs the RUNNER's
job_verify.py under a cron-like environment (env -i, minimal PATH, the Homebrew
Python cron resolves). So it tests what is deployed, not what is on disk in ~/Data --
a fix that is committed but not pulled into the runner fails here, on purpose.

Nothing it writes is outside its scratch directory; it never emits events or alerts.

  python3 .datacore/lib/laptop_night_drill.py        # exits 1 if any verdict is wrong

Override with NIGHT_DRILL_RUNNER, NIGHT_DRILL_PYTHON, NIGHT_DRILL_DIR.
"""
import os, subprocess, sys, time, textwrap
from datetime import datetime, timezone, timedelta
from pathlib import Path

import tempfile
HERE = Path(os.environ.get("NIGHT_DRILL_DIR") or tempfile.mkdtemp(prefix="laptop-night-drill-"))
RUNNER = Path(os.environ.get("NIGHT_DRILL_RUNNER") or Path.home() / ".datacore/v2-runner/.datacore/lib")
#: The interpreter cron resolves (job_verify_notify.sh tries Homebrew first), not this one.
PY = os.environ.get("NIGHT_DRILL_PYTHON") or next(
    (c for c in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3") if os.access(c, os.X_OK)), sys.executable)
NOW = time.time()
fails = []

def stamp(t):  # pmset's own format, local offset
    return datetime.fromtimestamp(t).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")

def power_log(events, noise=b""):
    lines = [f"{stamp(t)} {kind:<20}\tsynthetic\n".encode() for t, kind in events]
    return noise + b"".join(lines)

def scenario(name, *, artifact_age_h, max_h, events, content="OK phase1-cycle x rc=0\n",
             regex="^OK phase1-cycle", noise=b"", expect_ok, expect_text=None, roster_kind="workstation"):
    d = HERE / name
    import shutil; shutil.rmtree(d, ignore_errors=True)
    (d / "root/.datacore/registry").mkdir(parents=True)
    (d / "root/.datacore/registry/infrastructure.yaml").write_text(
        f"servers:\n  mac:\n    kind: {roster_kind}\n")
    art = d / "state/status.txt"; art.parent.mkdir(parents=True); art.write_text(content)
    mt = NOW - artifact_age_h * 3600; os.utime(art, (mt, mt))
    (d / "manifest.yaml").write_text(textwrap.dedent(f"""\
        version: 1
        jobs:
          - name: mac-sim
            machine: mac
            schedule: "sim"
            cmd: "true"
            artifacts:
              - path: "{art}"
                check: regex
                arg: '{regex}'
                max_age_hours: {max_h}
        """))
    bin_ = d / "bin"; bin_.mkdir()
    (d / "power.log").write_bytes(power_log(events(mt), noise))
    (bin_ / "pmset").write_text(f"#!/bin/sh\ncat '{d / 'power.log'}'\n"); (bin_ / "pmset").chmod(0o755)
    env = {"HOME": str(Path.home()), "PATH": f"{bin_}:/usr/bin:/bin", "DATACORE_V2": "1",
           "DATACORE_ROOT": str(d / "root"), "DATACORE_STATE": str(d / "jvstate")}
    r = subprocess.run([PY, str(RUNNER / "job_verify.py"), "--machine", "mac", "--manifest",
                        str(d / "manifest.yaml"), "--no-emit", "--alert", "log"],
                       env=env, capture_output=True, text=True, timeout=120, cwd="/tmp")
    out = (r.stdout + r.stderr).strip()
    ok = out.splitlines()[0].startswith("OK ") if out else False
    good = ok == expect_ok and (expect_text is None or expect_text in out)
    print(f"{'ok  ' if good else 'FAIL'} {name:44} verdict={'OK' if ok else 'FAILED'}"
          + ("" if good else f"\n       output: {out[:400]}"))
    if not good: fails.append(name)

H = 3600
# 1. The ordinary night: written, lid closed 6h, opened 1h ago. Awake age ~1.5h < 3h.
scenario("lid-closed-night-is-not-stale", artifact_age_h=7.5, max_h=3, expect_ok=True,
         events=lambda mt: [(mt + 0.5*H, "Sleep"), (mt + 6.5*H, "Wake")])
# 2. Night with dasd "Wake Requests" after every Sleep and DarkWakes all night -- the real shape.
def noisy(mt):
    ev = [(mt + 0.5*H, "Sleep")]
    t = mt + 0.5*H
    while t < mt + 6.4*H:
        ev += [(t + 2, "Wake Requests"), (t + 900, "DarkWake"), (t + 945, "Sleep")]
        t += 945
    return ev + [(mt + 6.5*H, "Wake")]
scenario("night-full-of-wake-requests-and-darkwakes", artifact_age_h=7.5, max_h=3, events=noisy, expect_ok=True)
# 3. Awake the whole time and the job still did not run: MUST fail.
scenario("awake-and-not-running-still-fails", artifact_age_h=4, max_h=3, events=lambda mt: [],
         expect_ok=False, expect_text="stale")
# 4. The 09:01 crash: undecodable bytes in the power log must not fail anything.
scenario("non-utf8-power-log-does-not-crash", artifact_age_h=7.5, max_h=3, expect_ok=True,
         noise=b"   pid 409(WindowServer): UserIsActive named: \xd5\xff tickle\n",
         events=lambda mt: [(mt + 0.5*H, "Sleep"), (mt + 6.5*H, "Wake")])
# 5. A server never gets sleep subtracted.
scenario("a-server-never-discounts-sleep", artifact_age_h=7.5, max_h=3, roster_kind="server",
         events=lambda mt: [(mt + 0.5*H, "Sleep"), (mt + 6.5*H, "Wake")], expect_ok=False, expect_text="stale")
# 6. Fresh artifact reporting a runtime failure: the cause must reach the alert text.
scenario("failed-status-names-its-cause", artifact_age_h=0.2, max_h=3, events=lambda mt: [],
         content="FAIL phase1-cycle x rc=127 (runtime init failed: no usable Python with PyYAML and org-workspace)\n",
         expect_ok=False, expect_text="no usable Python")
# 7. Sleep that began BEFORE the artifact was written and is still running into the window.
scenario("sleep-straddling-the-write", artifact_age_h=8, max_h=3, expect_ok=True,
         events=lambda mt: [(mt - 1*H, "Sleep"), (mt + 6*H, "Wake")])
# 8. Machine still asleep "now" (trailing Sleep): not guessed at -> counts as awake -> stale.
scenario("trailing-sleep-is-not-guessed", artifact_age_h=4, max_h=3, events=lambda mt: [(mt + 3.5*H, "Sleep")],
         expect_ok=False, expect_text="stale")

print(f"\nnight simulation: {8 - len(fails)}/8 as expected" + (f" -- FAILED: {', '.join(fails)}" if fails else ""))
sys.exit(1 if fails else 0)
