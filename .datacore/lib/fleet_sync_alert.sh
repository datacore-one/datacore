#!/usr/bin/env bash
# Send a Telegram alert when datacore-fleet-sync.service fails.
#
# Called via OnFailure= from the systemd unit. Reads TELEGRAM_BOT_TOKEN and
# TELEGRAM_CHAT_ID from the environment (injected by EnvironmentFile= in the
# unit). If credentials are absent it logs and exits non-zero so the failure
# is at least visible in journalctl.
#
# The last 30 lines of the fleet-sync journal are included so the alert is
# actionable without needing to SSH into the host.
set -uo pipefail

TOKEN="${TELEGRAM_BOT_TOKEN:-}"
CHAT="${TELEGRAM_CHAT_ID:-}"

if [[ -z "$TOKEN" || -z "$CHAT" ]]; then
    echo "fleet_sync_alert: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set — alert not sent" >&2
    exit 1
fi

RECENT="$(journalctl -u datacore-fleet-sync.service -n 30 --no-pager -o cat 2>/dev/null || true)"

python3 - "$TOKEN" "$CHAT" "$RECENT" <<'PYEOF'
import sys, urllib.request, urllib.parse, html

token  = sys.argv[1]
chat   = sys.argv[2]
recent = sys.argv[3] if len(sys.argv) > 3 else ""

# Trim and escape for Telegram HTML mode.
if len(recent) > 900:
    recent = "…" + recent[-900:]
recent_safe = html.escape(recent)

text = (
    "\U0001f6a8 <b>fleet-sync needs a human</b>\n\n"
    "One or more repos have pull conflicts or push failures — they are not converging.\n"
    "SSH into nightshift and run:\n\n"
    "<code>journalctl -u datacore-fleet-sync.service -n 50 --no-pager</code>\n\n"
    "Recent output:\n"
    f"<pre>{recent_safe}</pre>"
)

data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode()
try:
    with urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10
    ) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception as e:
    print(f"fleet_sync_alert: telegram send failed: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
