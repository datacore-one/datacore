"""MSG-6: A symbol such as "<" or "&" in a title never stops a message from being delivered.

Seeded failure (AM-07): remove html_safe/html.escape from an HTML sender and send a title with
"<" -- Telegram rejects the whole message. This holds today (html_safe, e80ae13); these evals are
regression guards for the root senders: the text reaches the phone intact, and a request in
parse_mode HTML carries no raw "<" or "&" outside the kept tags and entities.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _msg_harness as H  # noqa: E402

LIB = H.LIB
GROUP = "-100200"
TITLE = "a < b & c > d"


def _safe_for_mode(post):
    text = post["fields"]["text"]
    if post["fields"].get("parse_mode", "").upper() == "HTML":
        stripped = re.sub(r"</?(b|i|u|s|code|pre)>", "", text)
        assert "<" not in stripped and ">" not in stripped, text
        assert not re.search(r"&(?!(amp|lt|gt|quot|#\d+);)", stripped), text
    assert TITLE in H.visible(text) if post["fields"].get("parse_mode") else TITLE in text


def test_oauth_health_check_delivers_a_title_with_symbols(tmp_path):
    env = H.fake_env(tmp_path, TELEGRAM_BOT_TOKEN="000:miles", ALERT_CHAT_ID=GROUP)
    H.run_py(f"import sys; sys.path.insert(0, {str(LIB)!r}); import oauth_health_check as o; "
             f"o.send_telegram({TITLE!r})", env)
    posts = H.posts(tmp_path)
    assert len(posts) == 1
    _safe_for_mode(posts[0])


def test_fleet_sync_alert_delivers_a_journal_with_symbols(tmp_path):
    env = H.fake_env(tmp_path, TELEGRAM_BOT_TOKEN="000:miles", ALERT_CHAT_ID=GROUP, FAKE_JOURNAL=TITLE)
    H.run_sh(LIB / "fleet_sync_alert.sh", env=env)
    posts = H.posts(tmp_path)
    assert len(posts) == 1
    _safe_for_mode(posts[0])


def test_job_verify_notify_delivers_a_title_with_symbols(tmp_path):
    block = f"job 'mac-drills' FAILED:\n  - {TITLE}\nalert: job.verify FAILED: mac-drills (1 failure(s))\n"
    env = H.job_verify_notify_env(tmp_path, block, f"TELEGRAM_BOT_TOKEN=000:miles\nALERT_CHAT_ID={GROUP}\n")
    H.run_sh(LIB / "job_verify_notify.sh", "--machine", "mac", env=env)
    posts = H.posts(tmp_path)
    assert len(posts) == 1
    _safe_for_mode(posts[0])
