#!/usr/bin/env bash
# Send a Telegram alert when datacore-fleet-sync.service fails.
#
# Called via OnFailure= from the systemd unit. Reads TELEGRAM_BOT_TOKEN and
# ALERT_CHAT_ID from the environment (injected by EnvironmentFile= in the
# unit). Errors go only to The Firm group (MSG-1): there is no fallback to
# TELEGRAM_CHAT_ID, which is the agent's 1:1 chat with the owner. When the
# alert cannot be delivered -- no group, no token, a non-200 answer -- it is
# recorded as undelivered (tg_format.record_undelivered, read by the morning
# sweep) and this exits non-zero, so the failure is also in journalctl.
#
# The last lines of the fleet-sync journal are included, whole lines only and
# one phone screen in all (MSG-4, MSG-5): the tail used to be cut at
# recent[-900:], which opened the message mid-word.
set -uo pipefail

LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECENT="$(journalctl -u datacore-fleet-sync.service -n 30 --no-pager -o cat 2>/dev/null || true)"

python3 - "$LIB" "$RECENT" <<'PYEOF'
import json, os, socket, sys, time, urllib.error, urllib.parse, urllib.request

lib, recent = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""
sys.path.insert(0, lib)
try:
    from tg_format import clip, html_safe, record_undelivered
except Exception:  # noqa: BLE001 -- an old or missing formatter must not swallow the alert
    import html
    def clip(s, n):
        s = " ".join(str(s).split())
        return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"
    def html_safe(s):
        return html.escape(str(s), quote=False)
    def record_undelivered(sender, reason, text=""):
        path = os.environ.get("DATACORE_UNDELIVERED_LOG") or os.path.expanduser(
            "~/.datacore/state/undelivered-alerts.jsonl")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                     "host": socket.gethostname().split(".")[0], "sender": sender,
                                     "reason": reason, "text_head": clip(text, 200)}) + "\n")
        except OSError:
            pass

TAIL_LINES, LINE_CHARS = 8, 100
tail = [clip(l, LINE_CHARS) for l in recent.splitlines() if l.strip()][-TAIL_LINES:]
text = (
    "\U0001f6a8 <b>fleet-sync needs a human</b>\n"
    "Repos have pull conflicts or push failures and are not converging.\n"
    "On nightshift: <code>journalctl -u datacore-fleet-sync.service -n 50 --no-pager</code>\n"
    "Last lines:\n"
    f"<pre>{html_safe(chr(10).join(tail) or '(the journal is empty)')}</pre>"
)
head = "fleet-sync needs a human: " + (tail[-1] if tail else "")

token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("ALERT_CHAT_ID", "")
if not chat:
    why = "ALERT_CHAT_ID unset: alerts go only to The Firm group, never a 1:1 chat"
elif not token:
    why = "no bot token (TELEGRAM_BOT_TOKEN)"
else:
    why = ""
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10) as r:
            if r.status == 200:
                sys.exit(0)
            why = f"http {r.status}"
    except urllib.error.HTTPError as e:
        why = f"http {e.code}"
    except Exception as e:  # noqa: BLE001
        why = f"exception: {type(e).__name__}: {e}"
print(f"fleet_sync_alert: alert NOT delivered ({why}); recorded as undelivered", file=sys.stderr)
record_undelivered("fleet_sync_alert", why, head)
sys.exit(1)
PYEOF
