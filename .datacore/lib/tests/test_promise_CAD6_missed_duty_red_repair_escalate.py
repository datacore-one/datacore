"""CAD-6: A missed or failed duty shows red within its window. Its agent gets a
repair job, and I hear about it only if the repair fails three times or takes
24 hours.

Kind: deterministic. The daily liveness contract (cadence_liveness.collect at
the contract's DEFAULT_GRACE), and the morning repair chain (morning_repair
red_cadences -> delegate -> recheck) over a tmp Data root; the ledger append
and the Telegram alert are captured, never sent.

Seeded failure: a missed duty that stays green past its window; a repair item
addressed to nobody or to the wrong agent; an alert to the owner the same
night the repair was handed over. Verified by making liveness report nothing
and by dropping the item.
"""
from __future__ import annotations

import importlib.util
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("cl_cad6", ROOT / ".datacore" / "lib" / "cadence_liveness.py")
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)

import morning_repair as M  # noqa: E402

DAY = 86_400_000
SLUG = "cadence-plur-cio-geo-research"


def _root(tmp_path):
    sp = tmp_path / "5-plur"; (sp / ".datacore" / "events").mkdir(parents=True)
    (sp / "venture.yaml").write_text(yaml.safe_dump({"name": "plur", "stage": "growth", "nightshift": {"enabled": True}, "roles": {
        "cio": {"agent": "tris", "cadences": {"daily": ["geo-research"]}},
        "coo": {"agent": "miles", "cadences": {"daily": ["state-backup"]}}}}))
    with (sp / ".datacore" / "events" / "tris.jsonl").open("w") as fh:
        fh.write(json.dumps({"seq": 0, "hlc": f"{int(time.time() * 1000) - 3 * DAY}.0000.tris", "actor": "tris",
                             "type": "metric.attest", "payload": {"metric": "cadence.registration", "slugs": {SLUG: "46 5 * * *"}},
                             "prev": "", "hash": "h", "sig": "s"}) + "\n")
    return tmp_path, sp


@pytest.fixture(autouse=True)
def _placeholder_signatures(monkeypatch):
    monkeypatch.setattr(L, "_sig_ok", lambda e: e.sig == "s")
    import sys
    monkeypatch.setitem(sys.modules, "cadence_liveness", L)     # morning_repair imports it by name


def _contract_red(root):
    return L.collect(root, L.DEFAULT_GRACE, date.today())


def test_a_missed_duty_on_its_own_scheduler_is_red_within_about_a_window(tmp_path):
    root, _ = _root(tmp_path)                           # registered 3 days ago, never ran
    assert any("geo-research" in str(r[4]) for r in _contract_red(root))


def test_a_missed_miles_duty_is_red_within_about_a_window(tmp_path):
    """Last ran three days ago: two windows missed. The daily contract must show it."""
    root, sp = _root(tmp_path)
    log = sp / ".datacore" / "state" / "venture" / "cadence-log.yaml"; log.parent.mkdir(parents=True)
    last = (date.today() - timedelta(days=3)).isoformat()
    log.write_text(yaml.safe_dump({"coo": {"daily": {"state-backup": {"last_run": last, "result": "ok"}}}}))
    reds = [r for r in _contract_red(root) if "state-backup" in str(r[4])]
    assert reds, "a daily duty missed for two days is not red in the daily contract (grace 3 days past due)"


def _findings(tmp_path, monkeypatch):
    root, _ = _root(tmp_path)
    monkeypatch.setattr(M, "ROOT", root)
    found = [f for f in M.red_cadences() if "geo-research" in f["title"]]
    assert found, "the morning sweep does not see the red duty"
    return root, found[0]


def test_the_duty_owner_gets_the_repair_job(tmp_path, monkeypatch):
    root, f = _findings(tmp_path, monkeypatch)
    items = []
    import ledger.policy, ledger.log, actor_identity
    monkeypatch.setattr(ledger.policy, "guarded_append", lambda log, t, payload: items.append(payload))
    monkeypatch.setattr(ledger.log, "EventLog", lambda *a, **k: None)
    monkeypatch.setattr(actor_identity, "this_actor", lambda *a, **k: "winston")
    iid = M.delegate(f, date.today().isoformat())
    assert iid and items, f"no repair item: {f.get('delegation_error')}"
    assert items[0]["assignee"] == "tris", f"Tris's duty went to {items[0]['assignee']!r} for repair"


def test_the_owner_is_not_alerted_the_night_a_repair_is_handed_over(tmp_path, monkeypatch):
    root, f = _findings(tmp_path, monkeypatch)
    day = datetime.now(timezone.utc).date().isoformat()
    state, frags = tmp_path / "state", tmp_path / "fragments"; state.mkdir()
    monkeypatch.setattr(M, "STATE", state)
    monkeypatch.setattr(M, "FRAGMENTS", frags)
    (state / f"{day}.json").write_text(json.dumps({"swept_at": time.time() - 90 * 60, "findings": [
        {**f, "tried": "", "cleared_by_sweep": False, "item": f"repair-{f['id']}-{day.replace('-', '')}"}]}))
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [dict(f)])     # still failing at 03:30
    alerts = []
    monkeypatch.setattr(M, "_alert_group", lambda text: alerts.append(text))
    M.recheck()
    assert not alerts, ("the owner was alerted 90 minutes after the repair was handed over, "
                        f"before three failed attempts or 24 hours: {alerts[0][:200]!r}")
