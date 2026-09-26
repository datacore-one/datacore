"""MEM-67: Every AI session transcript is copied nightly, so past work can always be
recovered.

Kind: deterministic + production contract.
  * deterministic: the real nightly job (.datacore/lib/sync_traces.sh, cron 0 0 * * *) run
    with HOME in tmp against a fixture ~/.claude/projects holding a session transcript and
    that session's subagent transcripts (<session>/subagents/agent-*.jsonl, where Claude
    Code writes them): every one lands in the mirror.
  * production (mac, read-only): every transcript under ~/.claude/projects older than 26 h
    has a copy in 0-personal/traces/claude-code, and the job is scheduled.
  * production (fleet, read-only ssh): a host whose ~/.claude/projects holds transcripts
    (agents run Claude sessions there too) has a nightly job copying them.

Seeded failure: the job's glob narrowed to one project (copy skipped for the rest) -> the
second project's transcript is missing from the mirror; verified red.
"""
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
JOB = ROOT / ".datacore" / "lib" / "sync_traces.sh"
MIRROR = ROOT / "0-personal" / "traces" / "claude-code"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


def _run_job(home: Path, job: Path = JOB):
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    r = subprocess.run(["bash", str(job)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def _fixture(home: Path):
    projects = home / ".claude" / "projects"
    made = []
    for proj, sid in (("-Users-x-Data", "11111111-aaaa"), ("-Users-x-code-app", "22222222-bbbb")):
        top = projects / proj / f"{sid}.jsonl"
        sub = projects / proj / sid / "subagents" / "agent-a1b2c3.jsonl"
        sub.parent.mkdir(parents=True)
        top.write_text('{"type":"user","message":"hi"}\n')
        sub.write_text('{"type":"assistant","message":"subagent work"}\n')
        made += [top, sub]
    return projects, made


def test_every_transcript_including_subagents_reaches_the_mirror(tmp_path):
    projects, made = _fixture(tmp_path)
    _run_job(tmp_path)
    mirror = tmp_path / "Data" / "0-personal" / "traces" / "claude-code"
    missing = [str(p.relative_to(projects)) for p in made if not (mirror / p.relative_to(projects)).exists()]
    assert not missing, f"the nightly copy skips these transcripts: {missing}"


@pytest.mark.production
def test_the_mac_mirror_holds_every_transcript_older_than_a_night():
    cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30).stdout
    assert any("sync_traces.sh" in l and not l.lstrip().startswith("#") for l in cron.splitlines()), \
        "no scheduled nightly transcript copy on this machine"
    src = Path.home() / ".claude" / "projects"
    cutoff = time.time() - 26 * 3600
    missing = [str(p.relative_to(src)) for p in src.rglob("*.jsonl")
               if p.stat().st_mtime < cutoff and not (MIRROR / p.relative_to(src)).exists()]
    assert not missing, f"{len(missing)} transcripts older than 26 h are not in the mirror, e.g. {missing[:3]}"


@pytest.mark.production
@pytest.mark.parametrize("host", ["nightshift", "winston", "hermes", "plur-claw"])
def test_every_host_with_sessions_copies_them_nightly(host):
    r = subprocess.run([*SSH, host, "n=$(find ~/.claude/projects -name '*.jsonl' 2>/dev/null | wc -l); echo n=$n; "
                        "(crontab -l 2>/dev/null; systemctl list-timers --all --no-pager 2>/dev/null; "
                        "systemctl --user list-timers --all --no-pager 2>/dev/null) "
                        "| grep -i -E 'sync_traces|transcript|claude/projects' || true"],
                       capture_output=True, text=True, timeout=45)
    assert r.returncode == 0, f"{host}: could not read ({r.stderr.strip()[-160:]})"
    lines = r.stdout.splitlines()
    n = int(lines[0].split("=", 1)[1]) if lines and lines[0].startswith("n=") else 0
    if n == 0:
        return   # no AI sessions on this host: nothing to copy
    assert len(lines) > 1, f"{host} holds {n} session transcripts and no nightly job copies them"
