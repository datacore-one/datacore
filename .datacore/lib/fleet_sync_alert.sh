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
import os, socket, sys

lib, recent = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""
sys.path.insert(0, lib)
from tg_format import clip, deliver, html_safe   # the one sender; it records what it cannot deliver

TAIL_LINES, LINE_CHARS = 8, 100
tail = [clip(l, LINE_CHARS) for l in recent.splitlines() if l.strip()][-TAIL_LINES:]
text = (
    "\U0001f6a8 <b>fleet-sync needs a human</b>\n"
    "Repos have pull conflicts, push failures or stranded work and are not converging.\n"
    f"On {html_safe(socket.gethostname().split('.')[0])}: "
    "<code>journalctl -u datacore-fleet-sync.service -n 50 --no-pager</code>\n"
    "Last lines:\n"
    f"<pre>{html_safe(chr(10).join(tail) or '(the journal is empty)')}</pre>"
)
head = "fleet-sync needs a human: " + (tail[-1] if tail else "")

ok, why = deliver("fleet_sync_alert", text, os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                  os.environ.get("ALERT_CHAT_ID", ""), html=True, preformatted=True, summary=head, timeout=10)
if ok:
    sys.exit(0)
print(f"fleet_sync_alert: alert NOT delivered ({why}); recorded as undelivered", file=sys.stderr)
sys.exit(1)
PYEOF
