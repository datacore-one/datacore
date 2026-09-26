"""MSG-5: No message ever has a word or name cut off in the middle. Anything shortened ends cleanly with "…".

Seeded failure (AM-05): fleet_sync_alert.sh trims the journal with `recent[-900:]`, so the
message opens on a fragment such as "…l conflict: needs a human". The promise holds when every
journal line the alert quotes is a whole line of the journal, and when a long single-line relay
from job_verify_notify is shortened only between words.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _msg_harness as H  # noqa: E402

LIB = H.LIB
GROUP = "-100200"


def _fragments(text, words):
    """Tokens of `text` that are a strict piece of a journal word: a word cut in the middle.
    Journal words all look like w012x3, so any piece of one (x3, 012x3, w01) is recognisable."""
    bad = []
    for tok in text.split():
        t = tok.strip("…()[]<>.,;:")
        if t and not t.isdigit() and t not in words and any(t in w for w in words):
            bad.append(tok)
    return bad


def test_fleet_sync_alert_quotes_only_whole_journal_words(tmp_path):
    lines = [" ".join(f"w{i:03d}x{j}" for j in range(9)) + " pull conflict" for i in range(40)]
    journal = "\n".join(lines)
    words = {w for l in lines for w in l.split() if w.startswith("w")}
    assert _fragments("…" + journal[-900:], words), "the seed makes a tail cut land mid-word"
    env = H.fake_env(tmp_path, TELEGRAM_BOT_TOKEN="000:miles", ALERT_CHAT_ID=GROUP, FAKE_JOURNAL=journal)
    H.run_sh(LIB / "fleet_sync_alert.sh", env=env)
    posts = H.posts(tmp_path)
    assert len(posts) == 1
    shown = H.visible(posts[0]["fields"]["text"])
    assert any(w in shown.split() for w in words), "the alert quotes the journal"
    cut = _fragments(shown, words)
    assert not cut, f"cut word(s): {cut[:3]}"


def test_job_verify_notify_shortens_a_long_line_between_words(tmp_path):
    detail = " ".join(f"artifact{i}" for i in range(400))
    block = f"job 'mac-drills' FAILED:\n  - {detail}\nalert: job.verify FAILED: mac-drills (1 failure(s))\n"
    env = H.job_verify_notify_env(tmp_path, block, f"TELEGRAM_BOT_TOKEN=000:miles\nALERT_CHAT_ID={GROUP}\n")
    H.run_sh(LIB / "job_verify_notify.sh", "--machine", "mac", env=env)
    posts = H.posts(tmp_path)
    assert posts
    for p in posts:
        body = [l for l in p["fields"]["text"].splitlines() if "full report" not in l]
        tokens = [t for t in " ".join(body).split() if t.lstrip("•").startswith("artifact")]
        cut = [t for t in tokens if t.rstrip("…") not in detail.split()]
        assert not cut, f"cut word(s): {cut[:3]}"
