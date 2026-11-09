#!/usr/bin/env python3
"""Post one alert (stdin) to The Firm group through THIS agent's own Telegram bot.

Each machine sends its own alerts (owner decision 2026-09-30, "each machine for
itself"). A host's alert route is one line in its ~/.datacore/alerts.yaml, read
by job_verify (`--alert command`) and promise_nightly (send_to_firm):

    command: python3 ~/.datacore/v2-runner/.datacore/lib/agent_alert.py --bot tris-telegram-bot

The bot token and the group id are asked of the broker (creds.py get, beside
this file) at send time, so the route holds only credential ids, never a value.
Only the group (firm-alert-chat), never a 1:1 chat and never another bot (MSG-1).
One phone screen (MSG-4, tg_format.fit). A message that cannot be delivered is
recorded as undelivered for the morning sweep and exits 1 (MSG-10). A delivered
one prints Telegram's message id -- the proof of delivery.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(os.path.abspath(__file__)).parent   # not resolved: the broker beside THIS copy
GROUP_ID = "firm-alert-chat"
CONSUMER = "agent_alert"

try:
    from datacore.ledger import attests
except ImportError:  # a copy without .datacore/lib on the path: nothing to record into
    def attests(kind, **_kw):  # noqa: ANN001
        def _keep(fn):
            return fn
        return _keep


def _broker_get(cred_id: str) -> str:
    """The value the broker serves for `cred_id`; LookupError with the broker's reason when none."""
    try:
        r = subprocess.run([sys.executable, str(HERE / "creds.py"), "get", cred_id, "--consumer", CONSUMER],
                           capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as e:
        raise LookupError(f"broker could not be asked for {cred_id} ({type(e).__name__})") from None
    value = r.stdout.strip()
    if r.returncode != 0 or not value:
        why = (r.stderr.strip().splitlines() or [f"exit {r.returncode}"])[-1]
        raise LookupError(f"broker served no {cred_id}: {why}")
    return value


@attests("telegram.sent", detail="agent_alert: an agent's alert to The Firm group",
         when=lambda result, *a, **k: result[0] == 200)
def _post(token: str, chat: str, text: str) -> tuple[int, object]:
    """One sendMessage; (HTTP status, message id)."""
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data,
                                    timeout=30) as r:
            body = r.read()
            try:
                mid = (json.loads(body or b"{}").get("result") or {}).get("message_id")
            except ValueError:
                mid = None
            return r.status, mid
    except urllib.error.HTTPError as e:
        return e.code, None


def _undelivered(bot: str, reason: str, text: str) -> int:
    try:
        from tg_format import record_undelivered
        record_undelivered(f"agent_alert:{bot}", reason, text)
    except Exception:  # noqa: BLE001 -- already failing; say so on stderr at least
        pass
    print(f"agent_alert: NOT sent ({reason})", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bot", required=True, help="the broker id of this agent's own bot token")
    args = ap.parse_args(argv)
    text = sys.stdin.read().strip()
    if not text:
        print("agent_alert: nothing on stdin, nothing sent", file=sys.stderr)
        return 2
    try:
        import tg_format
        text = tg_format.fit(tg_format.normalize(text))
    except Exception:  # noqa: BLE001 -- a missing formatter must not stop the alert
        pass
    try:
        chat = _broker_get(GROUP_ID)
        token = _broker_get(args.bot)
    except LookupError as e:
        return _undelivered(args.bot, str(e), text)
    try:
        status, mid = _post(token, chat, text)
    except (OSError, ValueError) as e:
        return _undelivered(args.bot, f"telegram unreachable ({type(e).__name__})", text)
    if status != 200:
        return _undelivered(args.bot, f"telegram answered http {status}", text)
    print(f"agent_alert: sent to The Firm group by {args.bot} (http 200, message_id={mid})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
