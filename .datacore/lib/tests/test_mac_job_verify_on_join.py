"""The mac's own job verification runs on the join, not on a clock.

Owner decision 2026-10-05/07: the mac runs no clock-scheduled jobs at all. Its
job verification (job_verify_notify.sh, relaying failures to the always-on host)
ran from a `*/30` cron line that no manifest job declared, so removing the cron
line would have left this machine's jobs unjudged. It is a `trigger: join` duty
instead: every converged join judges this machine's jobs.
"""
import sys
from pathlib import Path

import yaml

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

MANIFEST = LIB / "jobs" / "manifest.yaml"


def _mac_jobs():
    return [j for j in yaml.safe_load(MANIFEST.read_text())["jobs"] if j.get("machine") == "mac"]


def test_the_mac_judges_its_own_jobs_on_every_join():
    verify = [j for j in _mac_jobs() if "job_verify_notify.sh" in str(j.get("cmd", ""))]
    assert len(verify) == 1, [j["name"] for j in verify]
    job = verify[0]
    assert job.get("trigger") == "join", job
    assert all(a.get("since") == "join" for a in job["artifacts"]), job["artifacts"]
