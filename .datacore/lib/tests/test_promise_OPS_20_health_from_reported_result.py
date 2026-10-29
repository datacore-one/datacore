"""OPS-20: a job's health is read from the result it reports, never from words in its output.

The owner's words: "A job's health is read from the result the job reports
about itself, never from words that happen to appear in its output, and a job
that did nothing is never counted as having worked."

Evidence (2026-10-28 fleet log review,
2-datacore/1-tracks/ops/reports/2026-10-28-fleet-logs-bug-classes-report.md):

  1. jobs/checks.py:58-61 and :357 -- the `no_crash` check fails a job when
     'Traceback' / 'No such file or directory' appears anywhere in the last 40
     lines of its log. A run that QUOTED an error message (an agent reporting
     what it had fixed) and then reported success was failed for it.
  2. sync_traces.sh + the `sync-traces` contracts (jobs/manifest.yaml,
     manifest.local.yaml:210-231) -- the stamp reads "sync_traces: 0
     transcript(s)" every night on hosts with nothing to copy, and the regex
     `sync_traces: \\d+ transcript` accepts 0, so a job that did nothing stays
     green.

Hermetic: the real check code and the real script, against tmp_path only
(HOME is pointed at tmp_path for the script and the artifact path).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from jobs.checks import run_check
from jobs.manifest import Artifact

LIB = Path(__file__).resolve().parents[1]


def test_a_quoted_error_message_does_not_fail_a_run_that_reported_success(tmp_path):
    """The run's own result line says it worked; an error message it quoted in
    passing is not evidence that the program died."""
    log = tmp_path / "repair.log"
    log.write_text(
        "2026-10-27T03:10:02Z start: nightly repair\n"
        "agent: the rsync step used to print \"No such file or directory\" for the\n"
        "agent: missing traces dir; that path is now created before the copy.\n"
        "agent: a user's report quoted 'Traceback (most recent call last)' from last week.\n"
        "2026-10-27T03:14:40Z result: ok -- 3 items repaired, exit 0\n")
    errors = run_check(Artifact(path=str(log), check="no_crash"))
    assert errors == [], (
        "the no_crash check failed a run whose own result line reports success, because an "
        f"error message it quoted appears in its log tail: {errors}")


def test_a_trace_sync_that_copied_nothing_is_not_counted_as_worked(tmp_path, monkeypatch):
    """A host with no Claude Code transcripts: sync_traces copies nothing and
    stamps "0 transcript(s)". The job's own contract must not call that a
    successful run."""
    home = tmp_path / "home"
    home.mkdir()
    dest = tmp_path / "traces"
    env = {**os.environ, "HOME": str(home), "TRACES_DEST": str(dest)}
    r = subprocess.run(["bash", str(LIB / "sync_traces.sh")], capture_output=True, text=True,
                       env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    stamp = home / ".datacore" / "state" / "sync-traces.last"
    assert "0 transcript" in stamp.read_text(), stamp.read_text()

    jobs = yaml.safe_load((LIB / "jobs" / "manifest.yaml").read_text())["jobs"]
    job = next(j for j in jobs if j["name"] == "sync-traces")
    monkeypatch.setenv("HOME", str(home))
    errors = [e for raw in job["artifacts"] for e in run_check(Artifact(**raw))]
    assert errors, (
        f"the sync-traces contract passed a run that copied nothing ({stamp.read_text().strip()!r}); "
        f"its regex {job['artifacts'][0].get('arg')!r} accepts '0 transcript(s)'")
