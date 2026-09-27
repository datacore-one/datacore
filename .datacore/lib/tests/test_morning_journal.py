"""morning_journal.py: the mac-morning-journal job.

Live failure 2026-09-23..27: launchd runs the script with /usr/bin/python3
(3.9). The script ran the 0-personal sync with sys.executable, the ledger does
not import on 3.9 (PEP 604 annotations), the sync crashed with a traceback, its
exit code was ignored, and the job then told the owner "journal not published
-- check nightshift": a local crash reported as the server's fault.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

LIB = Path(__file__).resolve().parents[1]


def _load(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("morning_journal_t", LIB / "morning_journal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE", tmp_path / "state")
    monkeypatch.setattr(mod, "JOURNALS", tmp_path / "journals")
    # A failed alert is recorded as undelivered; never in the real log.
    monkeypatch.setenv("DATACORE_UNDELIVERED_LOG", str(tmp_path / "undelivered.jsonl"))
    (tmp_path / "journals").mkdir()
    return mod


def test_the_sync_runs_under_an_interpreter_that_can_import_the_ledger():
    spec = importlib.util.spec_from_file_location("morning_journal_t2", LIB / "morning_journal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    py = mod.ledger_python()
    assert py, "no interpreter on this machine can import the ledger"
    r = subprocess.run([py, "-c", f"import sys; sys.path.insert(0, {str(LIB)!r}); import ledger.log"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_an_old_interpreter_is_not_used_for_the_sync(monkeypatch):
    spec = importlib.util.spec_from_file_location("morning_journal_t3", LIB / "morning_journal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_can_import_ledger", lambda py: py == "/good/python3")
    monkeypatch.setattr(mod, "_candidates", lambda: ["/usr/bin/python3", "/good/python3"])
    assert mod.ledger_python() == "/good/python3"


def test_a_failed_sync_is_reported_as_this_macs_sync_not_the_servers(monkeypatch, tmp_path, capsys):
    mod = _load(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "ledger_python", lambda: sys.executable)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if "ledger_transport.py" in " ".join(map(str, cmd)):
            return SimpleNamespace(returncode=1, stdout="", stderr="sync refused")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main() == 1
    out = capsys.readouterr().out
    assert "sync" in out and "on this Mac" in out, out
    assert "check nightshift" not in out, "a local sync failure blamed the server"


def test_a_good_sync_with_the_journal_present_opens_it(monkeypatch, tmp_path, capsys):
    mod = _load(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "ledger_python", lambda: sys.executable)
    from datetime import date
    (tmp_path / "journals" / f"{date.today().isoformat()}.md").write_text("x")
    opened = []

    def fake_run(cmd, **kw):
        if cmd[0] == "open":
            opened.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main() == 0
    assert opened


def test_a_hung_desktop_notification_does_not_crash_the_job(monkeypatch, tmp_path, capsys):
    """Live 2026-09-27: osascript timed out under launchd and the job died with a traceback."""
    mod = _load(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "ledger_python", lambda: sys.executable)

    def fake_run(cmd, **kw):
        if cmd[0] == "osascript":
            raise subprocess.TimeoutExpired(cmd, 10)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main() == 1          # journal absent: still a loud non-delivery, not a crash
    assert "NOT delivered" in capsys.readouterr().out


# ── a loud non-delivery goes to The Firm group, not only to the screen ─────────
#
# Live 2026-09-27: the "briefing NOT delivered" alert was a desktop notification,
# and osascript times out under launchd, so the loud alert never reached anyone.
# It now goes to The Firm group through the existing group sender
# (winston_send.py --alert on the roster's always-on host, the relay route
# job_verify_notify.sh uses); the desktop notification is a best-effort extra.

def _fake(calls, *, relay_rc=0, osascript_exc=None):
    def fake_run(cmd, **kw):
        calls.append((list(map(str, cmd)), kw))
        joined = " ".join(map(str, cmd))
        if "role-ssh" in joined:
            return SimpleNamespace(returncode=0, stdout="relayhost\n", stderr="")
        if cmd[0] == "ssh":
            return SimpleNamespace(returncode=relay_rc, stdout="", stderr="" if relay_rc == 0 else "ssh: refused")
        if cmd[0] == "osascript" and osascript_exc is not None:
            raise osascript_exc
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return fake_run


def _sends(calls):
    return [(c, kw) for c, kw in calls if c[0] == "ssh"]


def test_a_missing_briefing_is_alerted_to_the_firm_group(monkeypatch, tmp_path, capsys):
    mod = _load(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "ledger_python", lambda: sys.executable)
    monkeypatch.setenv("DATACORE_UNDELIVERED_LOG", str(tmp_path / "undelivered.jsonl"))
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", _fake(calls))
    assert mod.main() == 1
    sends = _sends(calls)
    assert len(sends) == 1, calls
    cmd, kw = sends[0]
    assert "relayhost" in cmd and "BatchMode=yes" in cmd
    assert cmd[-1].endswith("winston_send.py --alert"), "errors go only to The Firm group (--alert)"
    assert "NOT delivered" in kw["input"]
    assert kw.get("timeout"), "a relay with no time limit can hang the job"
    assert not (tmp_path / "undelivered.jsonl").exists(), "a delivered alert is not undelivered"


def test_a_failed_sync_is_alerted_to_the_firm_group(monkeypatch, tmp_path):
    mod = _load(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "ledger_python", lambda: None)   # nothing can import the ledger
    monkeypatch.setenv("DATACORE_UNDELIVERED_LOG", str(tmp_path / "undelivered.jsonl"))
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", _fake(calls))
    assert mod.main() == 1
    sends = _sends(calls)
    assert len(sends) == 1 and "on this Mac" in sends[0][1]["input"]


def test_an_alert_the_relay_cannot_deliver_is_recorded_as_undelivered(monkeypatch, tmp_path):
    mod = _load(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "ledger_python", lambda: sys.executable)
    log = tmp_path / "undelivered.jsonl"
    monkeypatch.setenv("DATACORE_UNDELIVERED_LOG", str(log))
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", _fake(calls, relay_rc=255))
    assert mod.main() == 1
    import json
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["sender"] == "morning_journal"
    assert "NOT delivered" in rows[0]["text_head"]


def test_the_desktop_notification_is_an_extra_that_can_never_block_or_crash(monkeypatch, tmp_path, capsys):
    mod = _load(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "ledger_python", lambda: sys.executable)
    monkeypatch.setenv("DATACORE_UNDELIVERED_LOG", str(tmp_path / "undelivered.jsonl"))
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", _fake(calls, osascript_exc=RuntimeError("no window server")))
    assert mod.main() == 1
    order = [c[0] for c, _ in calls if c[0] in ("ssh", "osascript")]
    assert order[0] == "ssh", "the group is told first; the screen is only an extra"
