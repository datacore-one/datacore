"""KNW-4: The research queue never grows forever. Items older than 60 days are cleared
out with a record, not silently deleted.

Kind: deterministic. The real auto_archive_stale_research() (step 0 of every run)
on a tmp research_learning.org.

Seeded failure: the stale item is deleted instead of moved to the dated archive with
an AUTO_ARCHIVED record; or an item without a :CREATED: date is exempt forever.
The second is live today: 24 of the 31 open items in the real queue carry no date,
and the archiver skips undated items on every pass ("they stay until they accrue a
date or get manually triaged"), so nothing ever gives them one.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _research_harness as H  # noqa: E402


def _queue(created_old, created_new):
    return f"""#+TITLE: Research
* Queue
** TODO Read: Ancient link
   :PROPERTIES:
   :ID: knw4-old
   :CREATED: [{created_old}]
   :END:
   Link: https://example.test/old
** TODO Read: Fresh link
   :PROPERTIES:
   :ID: knw4-new
   :CREATED: [{created_new}]
   :END:
   Link: https://example.test/new
** TODO Read: Undated link
   :PROPERTIES:
   :ID: knw4-undated
   :END:
   Link: https://example.test/undated
"""


def _open_ids(R, org):
    text = org.read_text(encoding="utf-8")
    return {i for i in ("knw4-old", "knw4-new", "knw4-undated") if f":ID: {i}" in text}


def test_items_older_than_60_days_move_to_a_dated_archive_with_a_record(tmp_path, monkeypatch):
    R = H.load()
    t = H.point_at(R, tmp_path, monkeypatch)
    today = dt.date.today()
    t.org.write_text(_queue((today - dt.timedelta(days=90)).isoformat(),
                            (today - dt.timedelta(days=10)).isoformat()), encoding="utf-8")

    moved = R.auto_archive_stale_research(max_age_days=60)

    assert moved == 1
    assert "knw4-old" not in _open_ids(R, t.org), "the stale item is still in the queue"
    assert {"knw4-new", "knw4-undated"} <= _open_ids(R, t.org), "a fresh item was cleared"
    archive = t.org.parent / ".archive" / f"research_learning-auto-archived-{today.isoformat()}.org"
    assert archive.is_file(), "no dated archive record was written"
    text = archive.read_text(encoding="utf-8")
    assert "Read: Ancient link" in text and "knw4-old" in text, "the stale item is not in the archive"
    assert f":AUTO_ARCHIVED: {today.isoformat()}" in text, "the archived item carries no AUTO_ARCHIVED record"


def test_an_undated_item_is_not_exempt_forever(tmp_path, monkeypatch):
    """Seen today with no date, it must still leave the queue once 60 days have passed."""
    R = H.load()
    t = H.point_at(R, tmp_path, monkeypatch)
    today = dt.date.today()
    t.org.write_text(_queue((today - dt.timedelta(days=5)).isoformat(),
                            (today - dt.timedelta(days=5)).isoformat()), encoding="utf-8")

    R.auto_archive_stale_research(max_age_days=60)          # first sight, today
    assert "knw4-undated" in _open_ids(R, t.org), "an undated item was cleared on first sight"

    later = today + dt.timedelta(days=61)

    class Later(dt.date):
        @classmethod
        def today(cls):
            return cls(later.year, later.month, later.day)

    monkeypatch.setattr(dt, "date", Later)                 # the function imports date locally
    R.auto_archive_stale_research(max_age_days=60)

    assert "knw4-new" not in _open_ids(R, t.org), "the clock did not move (eval harness fault)"
    assert "knw4-undated" not in _open_ids(R, t.org), (
        "an item without a :CREATED: date is still queued 61 days after the archiver first saw it; "
        "undated items are exempt from the 60-day drain forever")
