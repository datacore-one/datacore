"""MSG-10 / DAY-10: Nothing fails silently. An alert that cannot be delivered is reported the next morning.

Seeded failure (AM-20): give a sender an invalid bot token (HTTP 401), or no chat. Today
job_verify_notify writes "RELAY FAILED" only to its local log (and `curl -s` without a status
check counts a 401 as delivered), fleet_sync_alert prints to stderr, oauth_health_check
swallows the exception. The promise holds when every such failure appends one JSON line
{at, host, sender, reason, text_head} to ~/.datacore/state/undelivered-alerts.jsonl
($DATACORE_UNDELIVERED_LOG in tests), and morning_repair's `undelivered()` collector turns the
lines since the last sweep into "delivery" findings -- which reach the 03:30 re-check's
still_failing (the briefing's "needs you") and are never handed to Miles as a code repair
unless the reason is code.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _msg_harness as H  # noqa: E402

LIB = H.LIB
GROUP = "-100200"
KEYS = {"at", "host", "sender", "reason", "text_head"}


def _one(tmp_path, sender):
    rec = [r for r in H.undelivered(tmp_path) if sender in r.get("sender", "")]
    assert len(rec) == 1, H.undelivered(tmp_path)
    assert KEYS <= set(rec[0]), rec[0]
    return rec[0]


# ---- every root sender records its own failure ---------------------------------------

def test_job_verify_notify_records_a_rejected_send(tmp_path):
    env = H.job_verify_notify_env(tmp_path, H.alert_block("mac-drills"),
                                  f"TELEGRAM_BOT_TOKEN=000:bad\nALERT_CHAT_ID={GROUP}\n", FAKE_HTTP="401")
    H.run_sh(LIB / "job_verify_notify.sh", "--machine", "mac", env=env)
    rec = _one(tmp_path, "job_verify_notify")
    assert "401" in rec["reason"] and "mac-drills" in rec["text_head"]


def test_job_verify_notify_records_a_missing_token(tmp_path):
    env = H.job_verify_notify_env(tmp_path, H.alert_block("mac-drills"), f"ALERT_CHAT_ID={GROUP}\n")
    H.run_sh(LIB / "job_verify_notify.sh", "--machine", "mac", env=env)
    assert "token" in _one(tmp_path, "job_verify_notify")["reason"].lower()


def test_fleet_sync_alert_records_a_rejected_send_and_exits_non_zero(tmp_path):
    env = H.fake_env(tmp_path, TELEGRAM_BOT_TOKEN="000:bad", ALERT_CHAT_ID=GROUP, FAKE_HTTP="500",
                     FAKE_JOURNAL="pull conflict")
    r = H.run_sh(LIB / "fleet_sync_alert.sh", env=env)
    assert r.returncode != 0
    assert "500" in _one(tmp_path, "fleet_sync_alert")["reason"]


def test_oauth_health_check_records_a_rejected_send(tmp_path):
    env = H.fake_env(tmp_path, TELEGRAM_BOT_TOKEN="000:bad", ALERT_CHAT_ID=GROUP, FAKE_HTTP="401")
    r = H.run_py(f"import sys; sys.path.insert(0, {str(LIB)!r}); import oauth_health_check as o; "
                 f"o.send_telegram('google-work token expired')", env)
    assert r.returncode == 0, "a sender never crashes the calling job"
    rec = _one(tmp_path, "oauth_health_check")
    assert "401" in rec["reason"] and "google-work" in rec["text_head"]


def test_the_recorder_never_raises(monkeypatch):
    import tg_format
    monkeypatch.setenv("DATACORE_UNDELIVERED_LOG", "/dev/null/not-a-dir/x.jsonl")
    tg_format.record_undelivered("t", "http 401", "text")      # must not raise


# ---- the morning sweep reads them ----------------------------------------------------

def _line(at, sender, reason, text="an alert"):
    return json.dumps({"at": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "host": "box", "sender": sender,
                       "reason": reason, "text_head": text}) + "\n"


def _seed(tmp_path, monkeypatch, lines, last_sweep=None):
    import morning_repair as M
    log = tmp_path / "undelivered-alerts.jsonl"
    log.write_text("".join(lines))
    monkeypatch.setenv("DATACORE_UNDELIVERED_LOG", str(log))
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    if last_sweep is not None:
        (tmp_path / "state").mkdir(exist_ok=True)
        day = (last_sweep.date()).isoformat()
        (tmp_path / "state" / f"{day}.json").write_text(json.dumps({"swept_at": last_sweep.timestamp(),
                                                                    "findings": []}))
    return M


def test_lines_since_the_last_sweep_become_delivery_findings(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    last = now - timedelta(days=1)
    M = _seed(tmp_path, monkeypatch, [
        _line(last - timedelta(hours=2), "winston_send", "http 403", "before the last sweep"),
        _line(now - timedelta(hours=3), "cos_alert", "http 401", "briefing inputs missing"),
        _line(now - timedelta(hours=2), "cos_alert", "http 401", "mail triage failed"),
    ], last_sweep=last)
    found = M.undelivered()
    assert [f["kind"] for f in found] == ["delivery"]
    f = found[0]
    assert "cos_alert" in f["title"] and "401" in f["evidence"] and "2" in f["title"] + f["evidence"]
    assert "winston_send" not in json.dumps(found), "a line from before the last sweep was already reported"


def test_without_a_previous_sweep_the_last_day_is_read(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    M = _seed(tmp_path, monkeypatch, [_line(now - timedelta(days=3), "old_sender", "http 401"),
                                      _line(now - timedelta(hours=1), "fleet_sync_alert", "http 500")])
    found = M.undelivered()
    assert [f["kind"] for f in found] == ["delivery"] and "fleet_sync_alert" in found[0]["title"]


def test_the_collector_is_part_of_the_findings(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    M = _seed(tmp_path, monkeypatch, [_line(now - timedelta(hours=1), "winston_send", "http 401")])
    for name in ("failed_units", "red_cadences", "mail_triage", "escalations"):
        monkeypatch.setattr(M, name, lambda *a, **k: [])
    monkeypatch.setattr(M, "v2_checklist", lambda run=True: [])
    assert [f["kind"] for f in M.findings(run_v2=False)] == ["delivery"]


def test_a_delivery_failure_is_not_handed_to_miles_unless_the_reason_is_code(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    M = _seed(tmp_path, monkeypatch, [
        _line(now - timedelta(hours=1), "cos_alert", "http 401"),
        _line(now - timedelta(hours=1), "run", "exception: NameError: name 'clip' is not defined"),
    ])
    found = M.undelivered()
    monkeypatch.setattr(M, "pull_latest", lambda: "fleet sync rc 0")
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [dict(f) for f in found])
    monkeypatch.setattr(M, "remediate", lambda f: "")
    delegated = []
    monkeypatch.setattr(M, "delegate", lambda f, day: delegated.append(f["title"]) or "repair-x")
    M.sweep()
    assert len(delegated) == 1 and "run" in delegated[0], delegated


def test_the_0330_recheck_lists_an_undelivered_alert_as_still_failing(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    M = _seed(tmp_path, monkeypatch, [_line(now - timedelta(hours=1), "winston_send", "http 401")])
    monkeypatch.setattr(M, "FRAGMENTS", tmp_path / "frag")
    found = M.undelivered()
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [dict(f) for f in found])
    monkeypatch.setattr(M, "_alert_group", lambda text: None)
    M.recheck()
    day = now.date().isoformat()
    frag = json.loads((tmp_path / "frag" / day / "repairs.json").read_text())
    assert any("winston_send" in f["title"] for f in frag["still_failing"]), frag
