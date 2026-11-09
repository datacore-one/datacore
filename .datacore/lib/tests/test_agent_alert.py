"""agent_alert.py: an agent machine posts its own alerts to The Firm group through its own bot.

Owner decision 2026-09-30, "each machine for itself": Tris's and Data's machines were
silent (their job checks ran --alert log, their promise boards --no-send). The route is
an alert command in the host's ~/.datacore/alerts.yaml that runs this sender with the
agent's own bot credential id. The token and the group id are asked of the broker
(creds.py get) at send time and never stored in the route.

Every test runs the sender from a copy of the library with a fake broker beside it and
the Telegram fakes of _msg_harness first on the paths: nothing reaches the network.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _msg_harness as H  # noqa: E402

LIB = H.LIB
BOT_ID = "tris-telegram-bot"
TOKEN = "000:tris-own-bot"
GROUP = "-100200"
ONE_TO_ONE = "111"

_FAKE_BROKER = textwrap.dedent('''
    import json, os, sys
    args = sys.argv[1:]
    with open(os.environ["FAKE_BROKER_LOG"], "a") as fh:
        fh.write(json.dumps(args) + "\\n")
    if args[:1] != ["get"]:
        sys.exit(2)
    values = json.loads(os.environ.get("FAKE_CREDS", "{}"))
    cid = args[1]
    if cid not in values:
        print(f"creds get: not found: {cid}", file=sys.stderr)
        sys.exit(1)
    print(values[cid])
''')


def _lib(tmp_path: Path) -> Path:
    """The sender and its formatter, with a fake broker in creds.py's place."""
    lib = tmp_path / "lib"
    lib.mkdir(exist_ok=True)
    for name in ("agent_alert.py", "tg_format.py"):
        if not (lib / name).exists():
            (lib / name).symlink_to(LIB / name)
    (lib / "creds.py").write_text(_FAKE_BROKER)
    return lib


def _env(tmp_path: Path, creds: dict, **extra: str) -> dict:
    # DATACORE_ROOT names no install, so the sender's @attests records into no real ledger.
    return H.fake_env(tmp_path, FAKE_CREDS=json.dumps(creds), FAKE_BROKER_LOG=str(tmp_path / "broker.jsonl"),
                      DATACORE_ROOT=str(tmp_path / "no-install"), **extra)


def _send(tmp_path: Path, env: dict, text: str = "job 'hermes-creds-sync' FAILED", bot: str = BOT_ID):
    return subprocess.run([sys.executable, str(_lib(tmp_path) / "agent_alert.py"), "--bot", bot], input=text,
                          env=env, capture_output=True, text=True, timeout=60)


def _broker_asks(tmp_path: Path) -> list[list[str]]:
    p = tmp_path / "broker.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_posts_once_to_the_group_with_the_named_bot_and_reports_the_message(tmp_path):
    env = _env(tmp_path, {BOT_ID: TOKEN, "firm-alert-chat": GROUP}, TELEGRAM_CHAT_ID=ONE_TO_ONE)
    r = _send(tmp_path, env)
    assert r.returncode == 0, r.stderr
    posts = H.posts(tmp_path)
    assert [(p["token"], p["fields"].get("chat_id")) for p in posts] == [(TOKEN, GROUP)]
    assert "hermes-creds-sync" in posts[0]["fields"]["text"]
    assert "sent" in r.stdout and "message_id" in r.stdout
    assert TOKEN not in r.stdout + r.stderr, "the bot token was printed"
    assert not H.undelivered(tmp_path)


def test_token_and_group_come_from_the_broker_by_id(tmp_path):
    env = _env(tmp_path, {BOT_ID: TOKEN, "firm-alert-chat": GROUP})
    _send(tmp_path, env)
    asked = {a[1] for a in _broker_asks(tmp_path)}
    assert asked == {BOT_ID, "firm-alert-chat"}, asked
    assert all(a[0] == "get" and "--consumer" in a for a in _broker_asks(tmp_path))


def test_no_group_means_no_post_and_undelivered_never_a_1to1_chat(tmp_path):
    env = _env(tmp_path, {BOT_ID: TOKEN}, TELEGRAM_CHAT_ID=ONE_TO_ONE, ALERT_CHAT_ID="")
    r = _send(tmp_path, env)
    assert r.returncode != 0
    assert not H.posts(tmp_path), "an alert was posted without The Firm group"
    rec = H.undelivered(tmp_path)
    assert rec and "firm-alert-chat" in rec[0]["reason"], rec


def test_a_bot_the_broker_does_not_serve_is_not_replaced_by_another(tmp_path):
    env = _env(tmp_path, {"firm-alert-chat": GROUP}, TELEGRAM_BOT_TOKEN="000:someone-else")
    r = _send(tmp_path, env)
    assert r.returncode != 0
    assert not H.posts(tmp_path), "a bot other than the agent's own posted the alert"
    rec = H.undelivered(tmp_path)
    assert rec and BOT_ID in rec[0]["reason"], rec


def test_a_refused_send_is_recorded_undelivered_and_fails(tmp_path):
    env = _env(tmp_path, {BOT_ID: TOKEN, "firm-alert-chat": GROUP}, FAKE_HTTP="401")
    r = _send(tmp_path, env)
    assert r.returncode != 0
    rec = H.undelivered(tmp_path)
    assert rec and "401" in rec[0]["reason"], rec
    assert TOKEN not in json.dumps(rec) + r.stdout + r.stderr


def test_a_long_alert_is_one_phone_screen(tmp_path):
    env = _env(tmp_path, {BOT_ID: TOKEN, "firm-alert-chat": GROUP})
    text = "\n".join(f"job 'j{i}' FAILED: last line does not match" for i in range(60))
    assert _send(tmp_path, env, text=text).returncode == 0
    sent = H.posts(tmp_path)[0]["fields"]["text"]
    assert len(sent.splitlines()) <= 15 and len(sent) <= 1200


def test_job_verify_and_the_promise_board_deliver_through_it_as_the_host_command(tmp_path, monkeypatch):
    """The route the hosts install: ~/.datacore/alerts.yaml `command:` naming this sender."""
    env = _env(tmp_path, {BOT_ID: TOKEN, "firm-alert-chat": GROUP})
    for k in H.SCRUB:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    alerts = tmp_path / "alerts.yaml"
    alerts.write_text(f"command: {sys.executable} {_lib(tmp_path) / 'agent_alert.py'} --bot {BOT_ID}\n")
    monkeypatch.delenv("DATACORE_ALERT_COMMAND", raising=False)
    monkeypatch.setenv("DATACORE_ALERTS_FILE", str(alerts))
    sys.path.insert(0, str(LIB))
    import job_verify
    import promise_nightly
    assert job_verify._send_command("job 'hermes-creds-sync' FAILED") is True
    ok, why = promise_nightly.send_to_firm("A promise turned red on Tris's machine")
    assert ok, why
    assert [p["fields"].get("chat_id") for p in H.posts(tmp_path)] == [GROUP, GROUP]


def test_the_sender_records_what_it_sends():
    sys.path.insert(0, str(LIB))
    import agent_alert
    assert getattr(agent_alert._post, "__datacore_egress__", None), "agent_alert._post is not wrapped by @attests"
