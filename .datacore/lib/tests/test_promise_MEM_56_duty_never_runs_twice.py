"""MEM-56: A duty never runs twice at once. A second start while one is still running is
refused.

Kind: deterministic. The real execution envelope every scheduled job goes through
(.datacore/lib/jobs/run.py, "run.py <job>") with a tmp manifest (--manifest) declaring one
slow duty whose command records each start. Two starts, the second while the first is
still running (the 2026-09-01 double morning briefing; the 07:00 headless /today still
running when /today is typed at 09:00):
  * the second start is refused (non-zero exit), and
  * the duty's command ran exactly once.
Control: a start after the first has finished runs normally.

Seeded failure: none needed to show red today (the envelope has no single-flight guard);
the control proves the harness sees a normal run as a run, and a variant with the second
start after the first finished is green -- verified.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / ".datacore" / "lib" / "jobs" / "run.py"


def _manifest(tmp: Path) -> Path:
    starts, out = tmp / "starts.log", tmp / "out.txt"
    m = tmp / "manifest.yaml"
    m.write_text(f"""version: 1
jobs:
  - name: mac-slow-duty
    machine: mac
    schedule: "0 7 * * *"
    cmd: "echo start >> {starts}; sleep 4; date > {out}"
    timeout_seconds: 60
    artifacts:
      - path: "{out}"
        check: exists
        max_age_hours: 1
""")
    return m


def _start(manifest: Path):
    env = {**os.environ, "DATACORE_ROOT": str(ROOT), "DATACORE_MACHINE": "mac"}
    return subprocess.Popen([sys.executable, str(RUNNER), "mac-slow-duty", "--manifest", str(manifest)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)


def _starts(tmp: Path) -> int:
    p = tmp / "starts.log"
    return len(p.read_text().splitlines()) if p.exists() else 0


def test_a_second_start_while_running_is_refused(tmp_path):
    m = _manifest(tmp_path)
    first = _start(m)
    deadline = time.time() + 20
    while _starts(tmp_path) == 0 and time.time() < deadline:
        time.sleep(0.1)
    assert _starts(tmp_path) == 1, "the first start never began"
    second = _start(m)
    out2, _ = second.communicate(timeout=50)
    out1, _ = first.communicate(timeout=50)
    assert first.returncode == 0, out1
    assert _starts(tmp_path) == 1, (f"the duty ran twice at once ({_starts(tmp_path)} starts); "
                                    f"the second start was not refused: exit {second.returncode}\n{out2[-400:]}")
    assert second.returncode != 0, f"the refused start reported success:\n{out2[-400:]}"


def test_a_start_after_the_first_finished_runs(tmp_path):
    m = _manifest(tmp_path)
    for _ in range(2):
        p = _start(m)
        out, _ = p.communicate(timeout=50)
        assert p.returncode == 0, out
    assert _starts(tmp_path) == 2
