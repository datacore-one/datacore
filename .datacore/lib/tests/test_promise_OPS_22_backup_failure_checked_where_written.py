"""OPS-22: My backups and ledger checkpoints are made every day and proven restorable;
a day without a good one alerts me that morning.

Evidence (2026-10-28 fleet-logs bug-classes report, class 12): on Winston the ledger
checkpoint write failed with StateRootMismatch for 4 days while the ledger check stayed
green. ledger_daily.sh writes the checkpoint's outcome to its own files --
`ledger_checkpoint.py write > $STATE/checkpoint-write.log` and
`ledger_checkpoint.py verify > $STATE/checkpoint-verify.log` (and, on a failed write,
"checkpoint write rc=1; current backup not verified" to the cron log) -- but every job
in jobs/manifest.yaml that runs ledger_daily.sh reads some OTHER file:
box-ledger-verify reads only ledger-verify.log for '^OK ' (manifest.yaml:614-623),
box-projection-drift reads shadow-status.json, box-ledger-ingest reads
ledger-ingest.log. So no contract ever looked at the file where a checkpoint failure is
written, and it was never alerted.

Kind: static, manifest-level, read-only. The real manifest (with this install's
overlay, as jobs/manifest.effective_doc composes it) is read; the files each backup or
checkpoint writer leaves its result in are taken from the writer itself (the
redirections in ledger_daily.sh on the lines that run ledger_checkpoint.py, and the
`>>` target of a cos_backup.sh cron line), not typed in here.

Graded, for every machine where a job runs a backup or checkpoint writer:
- some job there has an artifact on the file where the writer's result is written,
- that artifact checks content (not just that the file exists or is non-empty),
- its max_age_hours is at most 26 (a missed day is red by the next morning),
- and the job's on_fail reaches the owner (telegram or the install's alert command).

Out of scope: the monthly knowledge-index pushes (mac-knowledge-*-backup), which are
monthly by owner decision 2026-10-02, not daily backups.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from jobs.manifest import effective_doc

LIB = Path(__file__).resolve().parents[1]
MANIFEST = LIB / "jobs" / "manifest.yaml"
MAX_AGE = 26
OWNER_REACHING = {"telegram", "command"}
CONTENT_CHECKS = {"regex", "last_line_regex", "json_has_keys"}  # jobs.manifest.CHECKS that read content


def _jobs() -> list[dict]:
    return (effective_doc(MANIFEST) or {}).get("jobs") or []


def _checkpoint_result_files() -> set[str]:
    """Basenames of the files ledger_daily.sh writes a checkpoint's outcome to."""
    text = (LIB / "ledger_daily.sh").read_text()
    out = set()
    for line in text.splitlines():
        if "ledger_checkpoint.py" in line and not line.lstrip().startswith("#"):
            m = re.search(r'>\s*"?\$STATE/([^"\s]+)"?', line)
            if m:
                out.add(m.group(1))
    assert out, "fixture: ledger_daily.sh no longer runs ledger_checkpoint.py with a result file"
    return out


def _good(job: dict, art: dict) -> bool:
    age = art.get("max_age_hours")
    return (art.get("check") in CONTENT_CHECKS
            and isinstance(age, (int, float)) and age <= MAX_AGE
            and job.get("on_fail") in OWNER_REACHING)


def test_every_checkpoint_writer_is_checked_on_the_file_its_failure_is_written_to():
    files = _checkpoint_result_files()
    writers = [j for j in _jobs() if "ledger_daily.sh" in str(j.get("cmd") or "")
               or "ledger_checkpoint.py" in str(j.get("cmd") or "")]
    assert writers, "fixture: no manifest job runs the checkpoint writer"
    problems = []
    for machine in sorted({j.get("machine") for j in writers}):
        mine = [j for j in writers if j.get("machine") == machine]
        hits = [(j, a) for j in mine for a in (j.get("artifacts") or [])
                if Path(str(a.get("path") or "")).name in files]
        if not any(_good(j, a) for j, a in hits):
            read = sorted({Path(str(a.get("path"))).name for j in mine
                           for a in (j.get("artifacts") or [])})
            problems.append(
                f"{machine}: jobs {[j['name'] for j in mine]} write the ledger checkpoint "
                f"(result in {sorted(files)}) but their contracts read only {read}"
                + (f"; the checks on the result file fall short: {[(j['name'], a) for j, a in hits]}"
                   if hits else ""))
    assert not problems, "a failed checkpoint would stay green:\n  " + "\n  ".join(problems)


def test_every_backup_job_is_checked_daily_on_its_own_log_and_alerts_the_owner():
    backups = [j for j in _jobs() if "cos_backup.sh" in str(j.get("cmd") or "")]
    assert backups, "fixture: no manifest job runs cos_backup.sh"
    problems = []
    for job in backups:
        m = re.search(r">>?\s*(\S+)", str(job["cmd"]))
        target = Path(m.group(1)).name if m else None
        arts = [a for a in (job.get("artifacts") or [])
                if target and Path(str(a.get("path") or "")).name == target]
        if not any(_good(job, a) for a in arts):
            problems.append(f"{job['name']}: result log {target!r}, on_fail={job.get('on_fail')!r}, "
                            f"artifacts={job.get('artifacts')}")
    assert not problems, "\n".join(problems)
