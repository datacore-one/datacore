"""MSG-4: Every message fits one phone screen. Longer detail sits in a saved report the message points to.

Seeded failure (AM-08): job_verify_notify relays every operator-facing failure block in full --
ten failing jobs made a 40-line message -- and fleet_sync_alert pastes 30 journal lines. The
promise holds when tg_format.fit(text, max_lines=15, max_chars=1200, more=<pointer>) keeps the
first lines, never cuts mid-word, and ends with "… full report: <pointer>", and the root senders'
alert paths post at most 15 lines / 1200 characters, pointing at where the rest is.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _msg_harness as H  # noqa: E402

LIB = H.LIB
GROUP = "-100200"
LONG = "\n".join(f"line {i:02d} of the verifier report, with the detail a person would need" for i in range(40))


def test_fit_keeps_the_first_lines_and_points_to_the_rest():
    from tg_format import fit
    got = fit(LONG, more="/srv/state/job_verify.log")
    lines = got.splitlines()
    assert len(lines) <= 15 and len(got) <= 1200
    assert lines[0] == LONG.splitlines()[0], "the first lines are kept"
    assert all(line in LONG.splitlines() for line in lines[:-1]), "kept lines are whole"
    assert lines[-1].startswith("…") and "full report: /srv/state/job_verify.log" in lines[-1]


def test_fit_leaves_a_message_that_fits_alone():
    from tg_format import fit
    assert fit("one\ntwo", more="/p") == "one\ntwo"


def test_fit_on_one_long_line_cuts_between_words():
    from tg_format import fit
    text = " ".join(f"word{i}" for i in range(900))
    got = fit(text, more="/p")
    assert len(got) <= 1200
    body = "\n".join(got.splitlines()[:-1])
    assert body and H.whole_words(body, text) == [], "a word was cut"
    assert "full report: /p" in got.splitlines()[-1]


def test_fit_limits_are_parameters_and_the_pointer_is_optional():
    from tg_format import fit
    got = fit(LONG, max_lines=5, max_chars=300)
    assert len(got.splitlines()) <= 5 and len(got) <= 300
    assert got.splitlines()[-1].startswith("…"), "a shortened message says so"


def test_job_verify_notify_relays_ten_failures_as_one_screen_with_a_pointer(tmp_path):
    blocks = "".join(H.alert_block(f"job-{i}") for i in range(10))
    env = H.job_verify_notify_env(tmp_path, blocks, f"TELEGRAM_BOT_TOKEN=000:miles\nALERT_CHAT_ID={GROUP}\n")
    H.run_sh(LIB / "job_verify_notify.sh", "--machine", "mac", env=env)
    posts = H.posts(tmp_path)
    assert len(posts) == 1, "one message"
    text = posts[0]["fields"]["text"]
    assert len(text.splitlines()) <= 15, f"{len(text.splitlines())} lines"
    assert len(text) <= 1200
    assert str(tmp_path / "jv.log") in text, "the message points to the full report"


def test_fleet_sync_alert_fits_one_screen(tmp_path):
    journal = "\n".join(f"fleet-sync: repo {i:02d} pulled, nothing to push" for i in range(30))
    env = H.fake_env(tmp_path, TELEGRAM_BOT_TOKEN="000:miles", ALERT_CHAT_ID=GROUP, FAKE_JOURNAL=journal)
    H.run_sh(LIB / "fleet_sync_alert.sh", env=env)
    posts = H.posts(tmp_path)
    assert len(posts) == 1
    shown = H.visible(posts[0]["fields"]["text"])
    assert len(shown.splitlines()) <= 15, f"{len(shown.splitlines())} lines"
    assert len(shown) <= 1200


def test_oauth_health_check_alert_fits_one_screen(tmp_path):
    env = H.fake_env(tmp_path, TELEGRAM_BOT_TOKEN="000:miles", ALERT_CHAT_ID=GROUP)
    code = (f"import sys; sys.path.insert(0, {str(LIB)!r}); import oauth_health_check as o; "
            f"o.send_telegram(sys.stdin.read())")
    H.run_py(code, env, stdin=LONG)
    posts = H.posts(tmp_path)
    assert len(posts) == 1
    shown = H.visible(posts[0]["fields"]["text"])
    assert len(shown.splitlines()) <= 15 and len(shown) <= 1200
