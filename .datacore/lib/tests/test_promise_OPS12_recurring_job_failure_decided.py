"""OPS-12: A job that fails 3 runs in a row is fixed or retired within 2 days. It is never
just alerted about again and again.

Kind: production. This machine's own record of recurring job failures
(jobs/recurrence.py, kept by job_verify.py on every machine that verifies jobs),
read as it stands tonight. Each machine judges itself (owner decision 2026-09-30).

"Fixed" means the job passed again, which resets its count. "Retired" means it left
the manifest, which drops its record (job_verify prunes jobs no longer declared).
Anything else, left recurring past the limit, is the failure this promise is about.

Why (2026-09-30): the promise scoreboard was ~210/214 green while the chief-of-staff
host had one job failing 187 runs in a row and two more near 80. The Mac had two more
failing since 09-26 and 09-28. Each was alerted, cooled down, and alerted again. No
promise covered it. Added red, owner-approved.
"""
import datetime
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: The owner's limit: a recurring failure has this many days to be fixed or retired.
DECIDE_WITHIN_DAYS = 2


def _overdue(rows: list[dict], today: datetime.date) -> list[str]:
    out = []
    for r in rows:
        first = r.get("first_failed")
        try:
            since = datetime.date.fromisoformat(str(first)[:10])
        except ValueError:
            out.append(f"{r['job']}: recurring ({r.get('consecutive')} runs) with no first-failure date")
            continue
        age = (today - since).days
        if age > DECIDE_WITHIN_DAYS:
            out.append(f"{r['job']}: failing {r.get('consecutive')} runs in a row since {since} "
                       f"({age} days)")
    return out


def test_the_limit_is_judged_by_age_not_by_count():
    """The rule itself, on fixed rows: a new streak is not yet overdue, an old one is."""
    today = datetime.date(2026, 9, 30)
    rows = [{"job": "fresh", "consecutive": 40, "first_failed": "2026-09-29"},
            {"job": "at-limit", "consecutive": 3, "first_failed": "2026-09-28"},
            {"job": "stale", "consecutive": 3, "first_failed": "2026-09-27"},
            {"job": "undated", "consecutive": 5, "first_failed": None}]
    got = {line.split(":")[0] for line in _overdue(rows, today)}
    assert got == {"stale", "undated"}, got


@pytest.mark.production
def test_no_recurring_failure_on_this_machine_outlives_the_limit():
    import json
    # This machine's REAL record: the test setup points DATACORE_STATE at a throwaway
    # folder, so the recurrence module would read an empty one here.
    record = Path.home() / ".datacore" / "state" / "job-verify-recurrence.json"
    if not record.exists():
        pytest.skip("this machine keeps no job-verification record")
    rows = [dict(job=k, **v) for k, v in json.loads(record.read_text()).items()
            if isinstance(v, dict) and v.get("recurring")]
    overdue = _overdue(rows, datetime.date.today())
    assert not overdue, (
        f"{len(overdue)} job(s) on this machine have kept failing past {DECIDE_WITHIN_DAYS} days "
        "without being fixed or retired: " + "; ".join(overdue))
