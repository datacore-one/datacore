"""MSG-1: Errors and alerts go only to The Firm group, never to the owner's 1:1 chats with the agents.

Seeded failure (AM-01): ALERT_CHAT_ID is unset in the sender's environment while the agent's
1:1 chat id (TELEGRAM_CHAT_ID) is set -- nightshift.env on 2026-09-25. Every root sender did
`ALERT_CHAT_ID or TELEGRAM_CHAT_ID`, so the error landed in Miles's 1:1 chat. The promise holds
when no request goes to the 1:1 chat, the alert is recorded as undelivered (MSG-10) naming
ALERT_CHAT_ID, and a shell sender exits non-zero. With the group set, the alert reaches it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _msg_harness as H  # noqa: E402

ONE_TO_ONE = "111"
GROUP = "-100200"
LIB = H.LIB


def _to_one_to_one(tmp_path):
    return [p for p in H.posts(tmp_path) if p["fields"].get("chat_id") == ONE_TO_ONE]


def _refused(tmp_path, sender):
    rec = [r for r in H.undelivered(tmp_path) if sender in r.get("sender", "")]
    return rec and "ALERT_CHAT_ID" in rec[0].get("reason", "")


def test_job_verify_notify_never_falls_back_to_a_1to1_chat(tmp_path):
    env = H.job_verify_notify_env(tmp_path, H.alert_block("mac-drills"),
                                  f"TELEGRAM_BOT_TOKEN=000:miles\nTELEGRAM_CHAT_ID={ONE_TO_ONE}\n")
    H.run_sh(LIB / "job_verify_notify.sh", "--machine", "mac", env=env)
    assert not _to_one_to_one(tmp_path), "an alert went to the agent's 1:1 chat"
    assert _refused(tmp_path, "job_verify_notify"), H.undelivered(tmp_path)


def test_job_verify_notify_posts_to_the_group_when_it_is_set(tmp_path):
    env = H.job_verify_notify_env(tmp_path, H.alert_block("mac-drills"),
                                  f"TELEGRAM_BOT_TOKEN=000:miles\nTELEGRAM_CHAT_ID={ONE_TO_ONE}\n"
                                  f"ALERT_CHAT_ID={GROUP}\n")
    H.run_sh(LIB / "job_verify_notify.sh", "--machine", "mac", env=env)
    assert [p["fields"].get("chat_id") for p in H.posts(tmp_path)] == [GROUP]
    assert not H.undelivered(tmp_path)


def test_fleet_sync_alert_refuses_without_the_group(tmp_path):
    env = H.fake_env(tmp_path, TELEGRAM_BOT_TOKEN="000:miles", TELEGRAM_CHAT_ID=ONE_TO_ONE,
                     FAKE_JOURNAL="1-datafund pull conflict")
    r = H.run_sh(LIB / "fleet_sync_alert.sh", env=env)
    assert not _to_one_to_one(tmp_path), "an alert went to the agent's 1:1 chat"
    assert r.returncode != 0
    assert _refused(tmp_path, "fleet_sync_alert"), H.undelivered(tmp_path)


def test_fleet_sync_alert_posts_to_the_group_when_it_is_set(tmp_path):
    env = H.fake_env(tmp_path, TELEGRAM_BOT_TOKEN="000:miles", TELEGRAM_CHAT_ID=ONE_TO_ONE, ALERT_CHAT_ID=GROUP,
                     FAKE_JOURNAL="1-datafund pull conflict")
    r = H.run_sh(LIB / "fleet_sync_alert.sh", env=env)
    assert r.returncode == 0, r.stderr
    assert [p["fields"].get("chat_id") for p in H.posts(tmp_path)] == [GROUP]


_OAUTH = ("import sys; sys.path.insert(0, {lib!r}); import oauth_health_check as o; "
          "o.send_telegram('google-work token expired; run the refresh')")


def test_oauth_health_check_never_falls_back_to_a_1to1_chat(tmp_path):
    env = H.fake_env(tmp_path, TELEGRAM_BOT_TOKEN="000:miles", TELEGRAM_CHAT_ID=ONE_TO_ONE)
    r = H.run_py(_OAUTH.format(lib=str(LIB)), env)
    assert r.returncode == 0, r.stderr
    assert not _to_one_to_one(tmp_path), "an alert went to the agent's 1:1 chat"
    assert _refused(tmp_path, "oauth_health_check"), H.undelivered(tmp_path)


def test_oauth_health_check_posts_to_the_group_when_it_is_set(tmp_path):
    env = H.fake_env(tmp_path, TELEGRAM_BOT_TOKEN="000:miles", TELEGRAM_CHAT_ID=ONE_TO_ONE, ALERT_CHAT_ID=GROUP)
    r = H.run_py(_OAUTH.format(lib=str(LIB)), env)
    assert r.returncode == 0, r.stderr
    assert [p["fields"].get("chat_id") for p in H.posts(tmp_path)] == [GROUP]
