"""MEM-67: Every AI session transcript is copied nightly, so past work can always be
recovered.

Kind: deterministic + production contract.
  * deterministic: the real nightly job (.datacore/lib/sync_traces.sh, cron 0 0 * * *) run
    with HOME in tmp against a fixture ~/.claude/projects holding a session transcript and
    that session's subagent transcripts (<session>/subagents/agent-*.jsonl, where Claude
    Code writes them): every one lands in the mirror.
  * production (mac, read-only): every transcript under ~/.claude/projects older than 26 h
    has a copy in 0-personal/traces/claude-code, and the job is scheduled: a cron line,
    or (the mac runs its duties on arrival since 2026-10-02, owner) the arrival job's
    stamp written within 48 h. Eval amended with the owner's approval, 2026-10-29.
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

import fleet_reach

ROOT = Path(__file__).resolve().parents[3]
JOB = ROOT / ".datacore" / "lib" / "sync_traces.sh"
MIRROR = ROOT / "0-personal" / "traces" / "claude-code"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
# The arrival job's stamp (jobs/manifest.yaml mac-sync-traces, max_age_hours 48).
STAMP = Path.home() / ".datacore" / "state" / "sync-traces.last"
STAMP_MAX_AGE = 48 * 3600


def _scheduled(cron: str, stamp: Path, now: float) -> bool:
    """A live cron line runs the copy, or the arrival job ran it within 48 h."""
    if any("sync_traces.sh" in l and not l.lstrip().startswith("#") for l in cron.splitlines()):
        return True
    try:
        return now - stamp.stat().st_mtime <= STAMP_MAX_AGE and "sync_traces:" in stamp.read_text()
    except OSError:
        return False


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


def test_an_arrival_run_counts_as_scheduled_only_while_its_stamp_is_fresh(tmp_path):
    """Since 2026-10-02 the mac runs its duties on arrival (visitor_join.py), not on a
    clock, so the copy is scheduled either by a cron line or by a fresh stamp from the
    manifest's arrival job. Seeded failure: no cron line and a stale stamp -> red."""
    stamp = tmp_path / "sync-traces.last"
    stamp.write_text("2026-10-29T10:33:57Z sync_traces: 5 transcript(s) in x\n")
    now = time.time()
    assert _scheduled("", stamp, now)
    os.utime(stamp, (now - 49 * 3600, now - 49 * 3600))
    assert not _scheduled("", stamp, now)
    assert _scheduled("0 0 * * * ~/Data/.datacore/lib/sync_traces.sh\n", stamp, now)
    assert not _scheduled("# 0 0 * * * ~/Data/.datacore/lib/sync_traces.sh\n", tmp_path / "none", now)


@pytest.mark.production
def test_the_mac_mirror_holds_every_transcript_older_than_a_night():
    cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30).stdout
    assert _scheduled(cron, STAMP, time.time()), \
        "no scheduled transcript copy on this machine (no cron line, and the arrival job's stamp is missing or older than 48 h)"
    src = Path.home() / ".claude" / "projects"
    cutoff = time.time() - 26 * 3600
    missing = [str(p.relative_to(src)) for p in src.rglob("*.jsonl")
               if p.stat().st_mtime < cutoff and not (MIRROR / p.relative_to(src)).exists()]
    assert not missing, f"{len(missing)} transcripts older than 26 h are not in the mirror, e.g. {missing[:3]}"


@pytest.mark.production
@pytest.mark.parametrize("host", ["nightshift", "box", "hermes", "plur-claw"])   # roster: host or k3s pod
def test_every_host_with_sessions_copies_them_nightly(host):
    for pl in fleet_reach.places(host):
        listing = fleet_reach.schedule_listing(pl) if pl.kind == "pod" else (
            "(crontab -l 2>/dev/null; systemctl list-timers --all --no-pager 2>/dev/null; "
            "systemctl --user list-timers --all --no-pager 2>/dev/null)")
        r = fleet_reach.run_at(pl, "n=$(find ~/.claude/projects -name '*.jsonl' 2>/dev/null | wc -l); echo n=$n; "
                               + listing + " | grep -i -E 'sync_traces|transcript|claude/projects' || true", timeout=45)
        assert r.returncode == 0, f"{host} ({pl.kind}): could not read ({r.stderr.strip()[-160:]})"
        lines = r.stdout.splitlines()
        n = int(lines[0].split("=", 1)[1]) if lines and lines[0].startswith("n=") else 0
        if n == 0:
            continue   # no AI sessions at this place: nothing to copy
        assert len(lines) > 1, f"{host} ({pl.kind}) holds {n} session transcripts and no nightly job copies them"
