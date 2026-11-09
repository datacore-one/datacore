"""The morning briefing's failures point at the host that owns it.

Fleet sim 2026-10-03, break 2: a reboot killed the always-on host's 04:00
briefing. Nothing re-ran it, and the Mac said "Morning briefing NOT delivered
(journal not published) -- check nightshift on the server": the briefing has
been the always-on host's job (box-briefing) since 2026-09-08, not nightshift's.

Break 10: a fallback read-out passed job_verify, whose audio contract only
asked whether the stamp file existed; the morning check went red on the same
morning. The two must not disagree.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import yaml

LIB = Path(__file__).resolve().parents[1]
MANIFEST = LIB / "jobs" / "manifest.yaml"


def _jobs():
    return {j["name"]: j for j in yaml.safe_load(MANIFEST.read_text())["jobs"]}


def test_the_briefing_has_a_catch_up_on_its_own_host():
    jobs = _jobs()
    c = jobs.get("box-briefing-catchup")
    assert c, "no catch-up: a reboot during the 04:00 run means no briefing that day"
    assert c["machine"] == jobs["box-briefing"]["machine"]
    assert "cos_morning_run.py catch-up" in c["cmd"]
    hours = c["schedule"].split()[1]
    assert hours.startswith("4") or hours.startswith("5"), "the catch-up runs inside the morning window"
    assert any(a.get("check") == "last_line_regex" for a in c.get("artifacts") or [])


def test_job_verify_reads_which_script_the_audio_spoke():
    arts = _jobs()["box-briefing"]["artifacts"]
    a = next((a for a in arts if str(a.get("path", "")).endswith("audio-script.json")), None)
    assert a, "job_verify must judge the audio as cos_verify_morning does, not only that it was sent"
    assert a["check"] == "regex" and "llm-native" in str(a["arg"])


def _load(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("morning_journal_h", LIB / "morning_journal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE", tmp_path / "state")
    monkeypatch.setattr(mod, "JOURNALS", tmp_path / "journals")
    monkeypatch.setenv("DATACORE_UNDELIVERED_LOG", str(tmp_path / "undelivered.jsonl"))
    (tmp_path / "journals").mkdir()
    return mod


def test_a_missing_journal_names_the_always_on_host_not_nightshift(monkeypatch, tmp_path, capsys):
    mod = _load(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "ledger_python", lambda: "/usr/bin/true")
    monkeypatch.setattr(mod, "_relay_host", lambda: "winston")
    sent = []
    monkeypatch.setattr(mod, "alert_group", lambda msg: sent.append(msg) or True)
    monkeypatch.setattr(mod, "notify", lambda msg: None)
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert mod.main() == 1
    msg = sent[0]
    assert "nightshift" not in msg, msg
    assert "winston" in msg and "box-briefing" in msg, msg
