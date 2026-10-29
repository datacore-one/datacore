"""After a usage limit resets, the liveness check stops saying "waiting on the usage limit".

2026-10-29 briefing: three meridian dailies (slots 09:30, 10:40, 13:07 UTC) last
stopped on "usage limit; resets 2pm (UTC)" on 10-28, before the 14:00 reset. The
next morning they were listed red as "waiting on quota for 2 day(s) ... nothing runs
until it resets" although the limit had already reset and their next slot had not
come yet. The owner's rule (2026-10-28): a usage limit is "waiting on quota", not a
failure. CAD-7 still holds -- a duty that has not really run for a window after the
limit is red -- but the window starts when the limit resets, and the words say so.
"""
from __future__ import annotations

import importlib.util
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("cl_reset", ROOT / ".datacore" / "lib" / "cadence_liveness.py")
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)

H = 3_600_000
DAY = 24 * H
SLUG = "cadence-plur-cio-geo-research"
REG = {"metric": "cadence.registration", "slugs": {SLUG: "46 5 * * *"}}


def _root(tmp_path):
    sp = tmp_path / "5-plur"; (sp / "drafts").mkdir(parents=True)
    (sp / "venture.yaml").write_text(yaml.safe_dump({"name": "plur", "stage": "growth", "nightshift": {"enabled": True},
                                                     "roles": {"cio": {"agent": "tris", "cadences": {"daily": ["geo-research"]}}}}))
    return tmp_path, sp


def _events(sp, records):
    d = sp / ".datacore" / "events"; d.mkdir(parents=True, exist_ok=True)
    with (d / "tris.jsonl").open("w") as fh:
        for i, (age_ms, payload) in enumerate(records):
            fh.write(json.dumps({"seq": i, "hlc": f"{int(time.time() * 1000) - age_ms}.0000.tris", "actor": "tris",
                                 "type": "metric.attest", "payload": payload, "prev": "", "hash": "h", "sig": "s"}) + "\n")


def _resets(at: datetime) -> str:
    h = at.hour % 12 or 12
    return f"usage limit; resets {at:%b} {at.day}, {h}{'pm' if at.hour >= 12 else 'am'} (UTC)"


def _quota(age_ms, reset: datetime):
    return (age_ms, {"metric": "cadence.run", "slug": SLUG, "phase": "end", "result": "quota", "reason": _resets(reset)})


@pytest.fixture(autouse=True)
def _placeholder_signatures(monkeypatch):
    monkeypatch.setattr(L, "_sig_ok", lambda e: e.sig == "s")


def _state(root):
    rows, grey = L.collect_states(root, grace=0, today=datetime.now(timezone.utc).date())
    red = [r for r in rows if "geo-research" in str(r[4])]
    if red:
        return "red", str(red[0][4])
    g = [x for x in grey if "geo-research" in str(x[4])]
    return ("amber", str(g[0][4])) if g else ("other", "")


def _hour(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def test_a_limit_that_has_reset_is_not_called_waiting_on_quota(tmp_path):
    root, sp = _root(tmp_path)
    now = datetime.now(timezone.utc)
    reset = _hour(now - timedelta(hours=10))            # reset this morning / last night
    _events(sp, [(5 * DAY, REG), _quota(3 * DAY, reset), _quota(2 * DAY, reset), _quota(DAY + 4 * H, reset)])
    state, msg = _state(root)
    assert state == "amber", f"a duty whose limit reset 10 h ago and whose next slot has not come is {state}: {msg}"
    assert "reset" in msg and "until it resets" not in msg, msg


def test_a_limit_still_in_force_waits_on_quota_until_its_reset(tmp_path):
    root, sp = _root(tmp_path)
    now = datetime.now(timezone.utc)
    reset = _hour(now + timedelta(hours=6))
    _events(sp, [(5 * DAY, REG), _quota(2 * DAY, reset), _quota(DAY + 4 * H, reset)])
    state, msg = _state(root)
    assert state == "amber" and "quota" in msg, (state, msg)


def test_no_real_run_for_a_window_after_the_reset_is_red_and_says_so(tmp_path):
    root, sp = _root(tmp_path)
    now = datetime.now(timezone.utc)
    reset = _hour(now - timedelta(hours=40))
    _events(sp, [(6 * DAY, REG), _quota(3 * DAY, reset), _quota(2 * DAY + 4 * H, reset)])
    state, msg = _state(root)
    assert state == "red", (state, msg)
    assert "reset" in msg and "waiting on quota" not in msg, msg
