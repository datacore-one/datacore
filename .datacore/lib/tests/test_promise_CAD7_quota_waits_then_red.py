"""CAD-7: A duty stopped by a usage limit shows as "waiting on quota", not as a
failure. It turns red if the window passes with no real run.

Kind: deterministic. The runner's classification (ventures cadence_run.main with
a fake runtime that hits its usage limit) and the liveness judge
(cadence_liveness.collect_states) over a tmp Data root.

Seeded failure: a usage limit recorded as `failed` (it would count toward the
three-strike trip and page as broken), or quota amber that never decays -- a
duty that hits its limit every day and never really runs stays amber forever.
Verified by making usage_limit_text return None, and by treating any quota
record as amber regardless of age.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("cl_cad7", ROOT / ".datacore" / "lib" / "cadence_liveness.py")
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)
import cadence_run as R  # noqa: E402

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


def _quota(age_ms):
    return (age_ms, {"metric": "cadence.run", "slug": SLUG, "phase": "end", "result": "quota", "reason": "usage limit"})


@pytest.fixture(autouse=True)
def _placeholder_signatures(monkeypatch):
    monkeypatch.setattr(L, "_sig_ok", lambda e: e.sig == "s")


def _where(root):
    rows, grey = L.collect_states(root, grace=0, today=datetime.now(timezone.utc).date())
    if any("geo-research" in str(r[4]) for r in rows):
        return "red", [r for r in rows if "geo-research" in str(r[4])]
    g = [x for x in grey if "geo-research" in str(x[4])]
    return ("amber" if g and "quota" in str(g[0][4]) else "other"), g


def test_a_fresh_usage_limit_waits_on_quota(tmp_path):
    root, sp = _root(tmp_path)
    _events(sp, [(3 * DAY, REG), _quota(H)])
    state, rows = _where(root)
    assert state == "amber" and "quota" in str(rows[0][4]), rows


def test_a_usage_limit_older_than_the_window_is_red(tmp_path):
    root, sp = _root(tmp_path)
    _events(sp, [(5 * DAY, REG), _quota(2 * DAY)])
    assert _where(root)[0] == "red"


def test_a_limit_hit_every_day_with_no_real_run_turns_red(tmp_path):
    """Ten days registered, a quota stop each morning, not one real run: the window has passed."""
    root, sp = _root(tmp_path)
    _events(sp, [(10 * DAY, REG)] + [_quota(d * DAY + H) for d in range(9, -1, -1)])
    state, rows = _where(root)
    assert state == "red", f"ten days without a real run still shows {state}: {rows}"


def test_the_runner_records_a_usage_limit_as_quota_not_failure(tmp_path, monkeypatch):
    root, sp = _root(tmp_path)
    firm = tmp_path / "8-firm"; (firm / ".datacore").mkdir(parents=True)
    (firm / "venture.yaml").write_text(yaml.safe_dump({"name": "firm", "stage": "growth", "roles": {}}))
    (firm / ".datacore" / "cadence-control.yaml").write_text(yaml.safe_dump(
        {"ceilings": {"tris": 3}, "executors": {"tris": "hermes"}}))
    tpl = tmp_path / "tpl"; tpl.mkdir()
    (tpl / "geo-research.md").write_text("---\ncadence: geo-research\nevidence:\n  path: drafts/*-{date}.md\n---\nGo.\n")
    written = []

    class Log:
        def __init__(self, *a, **k): pass
        def append(self, t, payload): written.append(dict(payload))
    import cadence_engine, executors, actor_identity, ledger.log
    monkeypatch.setattr(cadence_engine, "TEMPLATES_DIR", tpl)
    monkeypatch.setattr(ledger.log, "EventLog", Log)
    monkeypatch.setattr(R, "key_ok", lambda actor: None)
    monkeypatch.setattr(R, "report", lambda *a, **k: "not published (eval)")
    monkeypatch.setattr(executors, "get_executor", lambda n: SimpleNamespace(run=lambda prompt, **kw: SimpleNamespace(
        text="", error="You've hit your usage limit. Your limit resets at 3pm (UTC).")))
    monkeypatch.setattr(actor_identity, "this_actor", lambda strict=True: "tris")
    monkeypatch.setattr(sys, "argv", ["cadence_run.py", SLUG, "--root", str(root)])
    code = R.main()
    end = written[-1]
    assert end["phase"] == "end" and end["result"] == "quota" and code == 4, end
    # and a quota stop is not a strike toward the three-failure trip
    tail = [{"slug": SLUG, "phase": "end", "result": r, "_at": datetime.now(timezone.utc)}
            for r in ("failed", "quota", "quota", "failed")]
    monkeypatch.setattr(R, "runs", lambda space, actor: tail)
    assert R.refusal(R.find(root, SLUG), "tris", {"ceilings": {"tris": 99}}, root,
                     datetime.now(timezone.utc).date(), False) is None
