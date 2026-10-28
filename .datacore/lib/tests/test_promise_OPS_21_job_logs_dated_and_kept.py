"""OPS-21: Every line a scheduled job logs says when it happened, and a job's log
keeps its history instead of starting over each run.

Evidence (2026-10-28 fleet-logs report): the agent hosts' job_verify lines carry
no time since 09-06 -- `agent_host_setup.sh:122-140` installs cron lines that
append job_verify.py's raw output (`>> $STATE/job_verify.log 2>&1`) with no
timestamp; only `job_verify_notify.sh:64` writes a dated header, and the agent
hosts do not use it. And `ledger_phase1_cycle.sh:165` rewrites
phase1-ingest.log every run (`> "$STATE/phase1-ingest.log"`), keeping only the
' FAILED:' lines (:171), so when a conflict began cannot be told.

Kind: deterministic, hermetic.
- The cron line is the REAL one: agent_host_setup.sh runs for an agent-host
  profile (hermes, which owns a job-verify cron) against a throwaway HOME,
  runner and crontab (the stubbed `crontab` writes to a file), the way
  test_host_setup_dayshift_cycle does. The installed job-verify line is then
  executed as cron would run it, with job_verify.py replaced by a stub that
  prints plain lines; every line that lands in its log must carry a date and
  time. The runner's lib is otherwise the real one (symlinked), so a fix that
  routes output through a helper is exercised as installed.
- The ingest log check is STATIC: running ledger_phase1_cycle.sh needs a usable
  Python runtime, spaces in Phase 1, a converging transport and the ingest
  sweep, which is impractical hermetically. The script must not open
  phase1-ingest.log with a truncating redirect.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def _install_hermes(tmp_path):
    home = tmp_path / "home"
    runner = home / ".datacore" / "v2-runner"
    lib = runner / ".datacore" / "lib"
    registry = runner / ".datacore" / "registry"
    lib.mkdir(parents=True)
    registry.mkdir()
    # The real lib, so whatever the installed cron line calls exists -- except the
    # verifier itself (a stub that prints plain lines) and the roster lookup.
    for entry in LIB.iterdir():
        if entry.name in {"actor_identity.py", "job_verify.py", "tests", "__pycache__"}:
            continue
        (lib / entry.name).symlink_to(entry)
    registry.joinpath("infrastructure.yaml").write_text(
        "servers:\n  hermes:\n    access: {actor: tris}\n")
    home.joinpath(".datacore/identity.env").write_text("DATACORE_ACTOR=tris\n")
    lib.joinpath("actor_identity.py").write_text(
        "from pathlib import Path\n"
        f"REGISTRY_DIR = Path({str(registry)!r})\n"
        'if __name__ == "__main__":\n    print("tris fixture")\n')
    lib.joinpath("job_verify.py").write_text(
        "import sys\n"
        "print('hermes-heartbeat OK')\n"
        "print('tris-research FAIL artifact stale')\n"
        "print('summary: 1 failing', file=sys.stderr)\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # A wrapper, not a symlink: a symlinked venv interpreter loses its venv (and PyYAML).
    bindir.joinpath("python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    bindir.joinpath("python3").chmod(0o755)
    for name, body in (("systemctl", "exit 0"), ("sudo", "exit 1"), ("ssh", "exit 255"),
                       ("ssh-keyscan", "exit 1"),
                       ("crontab", 'case "$1" in -l) cat "$TEST_CRONTAB" ;; -) cat > "$TEST_CRONTAB" ;; *) exit 2 ;; esac')):
        bindir.joinpath(name).write_text(f"#!/bin/sh\n{body}\n")
        bindir.joinpath(name).chmod(0o755)
    store = tmp_path / "crontab"
    store.write_text("")
    state = home / ".datacore" / "state"
    env = {**os.environ, "HOME": str(home), "DATACORE_RUNNER": str(runner),
           "DATACORE_STATE": str(state), "TEST_CRONTAB": str(store),
           "PATH": str(bindir) + os.pathsep + os.environ["PATH"]}
    r = subprocess.run(["bash", str(LIB / "agent_host_setup.sh"), "--host", "hermes"],
                       env=env, capture_output=True, text=True, timeout=300)
    return store, env, state, r


def test_every_line_the_agent_host_verifier_logs_is_dated(tmp_path):
    store, env, state, setup = _install_hermes(tmp_path)
    lines = [l for l in store.read_text().splitlines() if "job_verify" in l]
    assert len(lines) == 1, f"no job-verify cron installed:\n{setup.stdout[-1500:]}{setup.stderr[-800:]}"
    command = lines[0].split(None, 5)[5]
    subprocess.run(["sh", "-c", command], env=env, capture_output=True, text=True, timeout=120)
    logs = [p for p in state.rglob("*") if p.is_file() and "job_verify" in p.name]
    assert logs, f"the cron line wrote no log: {command}"
    written = [l for p in logs for l in p.read_text().splitlines() if l.strip()]
    assert any("hermes-heartbeat OK" in l for l in written), written
    undated = [l for l in written if not STAMP.search(l)]
    assert not undated, (
        f"{len(undated)} of {len(written)} line(s) the agent-host verifier logged say "
        f"nothing about when they happened: {undated}")


def test_the_phase1_ingest_log_is_not_rewritten_each_run():
    text = (LIB / "ledger_phase1_cycle.sh").read_text()
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    truncating = [l.strip() for l in code.splitlines()
                  if re.search(r"(?<![>&0-9])>\s*\"?\$STATE/phase1-ingest\.log", l)]
    assert not truncating, (
        "ledger_phase1_cycle.sh truncates phase1-ingest.log every run, so its history "
        f"starts over: {truncating}")
