"""The weekly plan has to be readable by something other than a human.

On 2026-09-09 a week was planned in detail and nothing read it: no code
referenced the file, /today contained zero mentions of "weekly", and the fragment
directory held only health.json. The plan guided nobody.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "wpf", Path(__file__).resolve().parents[1] / "weekly_plan_fragment.py")
wpf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wpf)

PAYLOAD = {"week": "2026-W37", "thesis": "the fix is the multiplier",
           "priorities": [{"what": "the raise moves", "why": "zero meetings"}],
           "predictions": ["the benchmark will not win"],
           "days": {date.today().isoformat(): "maker day"}}


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(wpf, "FRAGMENTS", tmp_path)


def test_written_then_read_back(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    wpf.write(PAYLOAD)
    got = wpf.read()
    assert got["week"] == "2026-W37"
    assert got["_age_days"] == 0


def test_it_follows_the_fragment_contract(tmp_path, monkeypatch):
    """Same shape The Practice already writes, so the briefing can consume it
    like any other fragment rather than needing a special case."""
    _isolate(tmp_path, monkeypatch)
    p = wpf.write(PAYLOAD)
    doc = json.loads(p.read_text())
    for field in ("schema_version", "composed_at", "composed_by", "date"):
        assert field in doc, field
    assert doc["composed_by"] == "weekly-plan"
    assert p.name == "weekly-plan.json"


def test_a_plan_written_monday_is_found_on_friday(tmp_path, monkeypatch):
    """Written once a week, read every day — the whole point."""
    _isolate(tmp_path, monkeypatch)
    monday = date.today() - timedelta(days=4)
    wpf.write(PAYLOAD, monday)
    got = wpf.read()
    assert got is not None and got["_age_days"] == 4
    assert not got["_expired"]


def test_a_plan_older_than_the_week_it_planned_is_not_returned(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    wpf.write(PAYLOAD, date.today() - timedelta(days=9))
    assert wpf.read() is None


def test_no_plan_returns_none_rather_than_inventing_one(tmp_path, monkeypatch):
    """A missing plan must stay visibly missing. The failure this replaces was a
    briefing filling the gap from the task list and calling it a plan."""
    _isolate(tmp_path, monkeypatch)
    assert wpf.read() is None


def test_the_render_carries_what_a_briefing_needs(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    wpf.write(PAYLOAD)
    out = wpf._render(wpf.read())
    assert "2026-W37" in out
    assert "the raise moves" in out
    assert "the benchmark will not win" in out
    assert "maker day" in out
