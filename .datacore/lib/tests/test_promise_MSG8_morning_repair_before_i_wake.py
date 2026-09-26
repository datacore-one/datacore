"""MSG-8: Before I wake, the morning repair re-runs what it safely can and hands the rest to Miles. My briefing lists only what still needs me.

Kind: deterministic + production.

Deterministic: the real `morning_repair.sweep` and `morning_repair.recheck`
over tmp state/fragment dirs. Stood in for: the collectors' raw sources (which
findings exist before and after the safe re-run), the safe re-run itself, the
ledger append a delegation makes (captured), the group alert, and `gh` (a fake
on PATH that knows of no pull request).

Production (@production): on the box, read-only over ssh -- the 02:00 sweep and
03:30 recheck are scheduled, and the latest fragment was written before 04:00
UTC by a sweep that ran after 02:00.

Seeded failure: four findings -- a failed one-shot unit that one re-run clears,
a checklist failure nothing safe can clear, a repair Miles already gave up on,
and a delivery failure only a person can fix. The promise holds when the unit
is re-run and reported repaired (not as needing me), only the checklist failure
goes to Miles (with the never-merge rule), and the briefing's still-failing
list holds exactly what needs me -- without telling me to review a pull request
that does not exist.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import morning_repair as M  # noqa: E402

UNIT = {"id": "unit-x", "kind": "unit", "unit": "x.service", "title": "unit x.service failed",
        "evidence": "rc 1"}
V2 = {"id": "v2-hash-chains", "kind": "v2", "title": "v2-verify: hash chains", "evidence": "7/9 verify"}
ESC = {"id": "escalation-box-y", "kind": "escalation", "title": "box-y: miles gave up",
       "evidence": "gave up after 3 failed attempts"}
DELIV = {"id": "delivery-group", "kind": "delivery", "title": "an alert reached nobody",
         "evidence": "chat not found"}


@pytest.fixture
def night(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    monkeypatch.setattr(M, "FRAGMENTS", tmp_path / "frag")
    monkeypatch.setattr(M, "pull_latest", lambda: "fleet sync rc 0")
    b = tmp_path / "bin"
    b.mkdir()
    (b / "gh").write_text("#!/bin/sh\necho '[]'\n")
    (b / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{b}:{os.environ['PATH']}")
    rerun = []

    def remediate(f):          # the real one runs systemctl; the fixture records the safe re-run
        if f["kind"] == "unit":
            rerun.append(f["unit"])
            return f"re-ran {f['unit']} (rc 0)"
        return ""
    monkeypatch.setattr(M, "remediate", remediate)
    cleared = {"done": False}

    def findings(run_v2=True):
        now = [dict(V2), dict(ESC), dict(DELIV)]
        return now if cleared["done"] or rerun else [dict(UNIT)] + now
    monkeypatch.setattr(M, "findings", findings)
    items = []
    import ledger.policy
    monkeypatch.setattr(ledger.policy, "guarded_append",
                        lambda log, kind, payload: items.append(payload))
    import actor_identity
    monkeypatch.setattr(actor_identity, "this_actor", lambda: "winston")
    import jobs.autofix
    monkeypatch.setattr(jobs.autofix, "_space", lambda root: tmp_path / "space")
    alerts = []
    monkeypatch.setattr(M, "_alert_group", alerts.append)
    M.sweep()
    M.recheck()
    day = M.datetime.now(M.timezone.utc).date().isoformat()
    frag = json.loads((tmp_path / "frag" / day / "repairs.json").read_text())
    return rerun, items, frag, alerts


def test_what_is_safe_is_re_run_and_reported_repaired(night):
    rerun, items, frag, _ = night
    assert rerun == ["x.service"]
    assert [r["title"] for r in frag["repaired"]] == [UNIT["title"]]
    assert UNIT["title"] not in [f["title"] for f in frag["still_failing"]]


def test_the_rest_goes_to_miles_with_the_pr_rule_and_nothing_twice(night):
    _, items, _, _ = night
    assert [i["assignee"] for i in items] == ["miles"]
    assert items[0]["title"] == f"Repair: {V2['title']}"
    assert "do not merge" in items[0]["body"]


def test_the_briefing_lists_only_what_still_needs_me(night):
    _, items, frag, _ = night
    listed = {f["title"]: f for f in frag["still_failing"]}
    assert set(listed) == {V2["title"], ESC["title"], DELIV["title"]}
    assert "a person" in listed[ESC["title"]]["needs"]
    assert "a person" in listed[DELIV["title"]]["needs"]


def test_it_never_asks_me_to_review_a_pull_request_that_does_not_exist(night):
    _, items, frag, _ = night
    v2 = next(f for f in frag["still_failing"] if f["title"] == V2["title"])
    assert "pull request" not in v2["needs"], (
        f"no pull request names {items[0]['id']}, yet the briefing says: needs {v2['needs']!r}")


BOX = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "winston"]


@pytest.mark.production
def test_on_the_box_the_repair_runs_before_the_briefing():
    cmd = ("crontab -l | grep -c 'morning_repair.py sweep' ; crontab -l | grep 'morning_repair.py' ; "
           "f=$(ls -1 ~/.datacore/cos/fragments/*/repairs.json | sort | tail -1); echo FRAG $f; cat $f; "
           "s=$(ls -1 ~/.datacore/state/morning-repair/*.json | sort | tail -1); echo; echo SWEPT "
           "$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))[\"swept_at\"])' $s)")
    r = subprocess.run([*BOX, cmd], capture_output=True, text=True, timeout=45)
    assert r.returncode == 0, r.stderr[-300:]
    out = r.stdout
    assert "0 2 * * *" in out and "sweep" in out, "the 02:00 UTC sweep is not scheduled on the box"
    assert "30 3 * * *" in out and "recheck" in out, "the 03:30 UTC recheck is not scheduled on the box"
    frag = json.loads(out[out.index("{", out.index("FRAG")):out.rindex("}") + 1])
    from datetime import datetime, timezone
    checked = datetime.fromisoformat(frag["checked_at"])
    swept = datetime.fromtimestamp(float(out.rsplit("SWEPT", 1)[1].strip()), timezone.utc)
    assert frag["date"] == checked.date().isoformat()
    assert checked.hour < 4, f"the re-check finished at {checked} -- after the 04:00 briefing"
    assert swept.date() == checked.date() and 2 <= swept.hour < 4 and swept < checked, \
        f"the sweep ran at {swept}, not between 02:00 and the re-check"
    assert (datetime.now(timezone.utc) - checked).total_seconds() < 30 * 3600, \
        f"the latest re-check is from {checked}: the morning repair did not run today"
