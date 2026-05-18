#!/usr/bin/env python3
"""OAuth token health check — proactively warn before tokens expire.

Scans Google OAuth token files under .datacore/env/credentials/ and reports
which tokens are expired, expiring soon, or still failing to refresh.

Designed to run as part of the daily nightshift-health-audit timer (06:30 UTC)
so the user gets a Telegram alert one or more days BEFORE /today fails.

Usage:
    python3 oauth_health_check.py                # report only
    python3 oauth_health_check.py --refresh      # try to refresh expired tokens
    python3 oauth_health_check.py --telegram     # send results to Telegram
    python3 oauth_health_check.py --warn-days 7  # warn threshold (default 7)

Exit codes:
    0 — all healthy
    1 — at least one token expired or refresh failed
    2 — at least one token expiring within warn-days
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CREDS_DIR = Path(__file__).parent.parent / "env" / "credentials"


def parse_expiry(expiry_str: str):
    """Parse the various ISO-8601 forms Google emits."""
    if not expiry_str:
        return None
    try:
        s = expiry_str.replace('Z', '+00:00')
        return datetime.fromisoformat(s)
    except Exception:
        return None


def try_refresh(token_data: dict, token_path: Path):
    """Attempt to refresh credentials. Returns (ok, new_expiry, error)."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        return False, None, "google-auth library not installed"

    try:
        # Use the file's stored scopes — None falls back to whatever the token has.
        creds = Credentials.from_authorized_user_info(token_data, token_data.get('scopes'))
        if not creds.refresh_token:
            return False, None, "no refresh_token in file"
        creds.refresh(Request())
        return True, creds.expiry, None
    except Exception as e:
        return False, None, str(e)


def send_telegram(text: str):
    """Send notification via Telegram if configured."""
    bot = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat = os.environ.get('TELEGRAM_CHAT_ID')
    if not bot or not chat:
        return
    try:
        data = urllib.parse.urlencode({'chat_id': chat, 'text': text, 'parse_mode': 'HTML'}).encode()
        req = urllib.request.Request(f'https://api.telegram.org/bot{bot}/sendMessage', data=data)
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="OAuth token health check")
    parser.add_argument('--refresh', action='store_true', help='attempt to refresh tokens during check')
    parser.add_argument('--telegram', action='store_true', help='send results to Telegram')
    parser.add_argument('--warn-days', type=int, default=7, help='warn threshold in days (default 7)')
    parser.add_argument('--quiet', action='store_true', help='only print problems')
    args = parser.parse_args()

    if not CREDS_DIR.exists():
        print(f"No credentials directory at {CREDS_DIR}", file=sys.stderr)
        return 0

    # Discover all Google token files. Pattern: gmail_token_*.{json,pickle},
    # google_calendar_token*.json. Pickle files are legacy; we don't try to
    # decode them, just report their presence.
    token_files = sorted(
        list(CREDS_DIR.glob('google_calendar_token*.json'))
        + list(CREDS_DIR.glob('gmail_token_*.json'))
    )
    pickle_files = sorted(list(CREDS_DIR.glob('gmail_token_*.pickle')))

    now = datetime.now(timezone.utc)
    rows = []  # (name, state, msg)
    exit_code = 0
    problems = []

    for f in token_files:
        try:
            d = json.loads(f.read_text())
        except Exception as e:
            rows.append((f.name, 'UNREADABLE', str(e)))
            problems.append(f"{f.name}: unreadable ({e})")
            exit_code = max(exit_code, 1)
            continue

        expiry = parse_expiry(d.get('expiry', ''))
        has_refresh = bool(d.get('refresh_token'))

        if not expiry:
            rows.append((f.name, 'NO_EXPIRY', 'cannot parse expiry'))
            continue

        delta = expiry - now
        days_left = delta.total_seconds() / 86400

        # First: classify by clock state
        if days_left < 0:
            # Expired. Try refresh to know whether it's truly broken or just stale-but-fixable.
            if args.refresh and has_refresh:
                ok, new_exp, err = try_refresh(d, f)
                if ok:
                    rows.append((f.name, 'REFRESHED', f"new expiry {new_exp.isoformat()}"))
                    continue
                else:
                    rows.append((f.name, 'REFRESH_FAILED', err))
                    problems.append(f"{f.name}: refresh failed — {err}")
                    exit_code = max(exit_code, 1)
            else:
                # Even without --refresh, try a refresh to learn whether the
                # token is fixable. We only WRITE if --refresh was set.
                if has_refresh:
                    ok, new_exp, err = try_refresh(d, f)
                    if ok:
                        rows.append((f.name, 'EXPIRED_FIXABLE', f"refresh works; re-auth not needed (run with --refresh to persist)"))
                        problems.append(f"{f.name}: expired but refresh works — run with --refresh")
                        exit_code = max(exit_code, 2)
                    else:
                        rows.append((f.name, 'EXPIRED_BROKEN', err))
                        problems.append(f"{f.name}: expired AND refresh broken — manual re-auth needed: {err}")
                        exit_code = max(exit_code, 1)
                else:
                    rows.append((f.name, 'EXPIRED_NO_REFRESH', 'no refresh_token in file'))
                    problems.append(f"{f.name}: expired and has no refresh_token")
                    exit_code = max(exit_code, 1)
        elif days_left < args.warn_days:
            rows.append((f.name, 'WARN', f"{days_left:.1f} days left"))
            problems.append(f"{f.name}: expires in {days_left:.1f} days")
            exit_code = max(exit_code, 2)
        else:
            rows.append((f.name, 'OK', f"{days_left:.1f} days left"))

    for p in pickle_files:
        rows.append((p.name, 'LEGACY_PICKLE', 'consider migrating to JSON'))

    # Render report
    width = max(len(r[0]) for r in rows) if rows else 30
    lines = [f"OAuth token health check — {now.isoformat()}",
             f"Credentials dir: {CREDS_DIR}",
             ""]
    for name, state, msg in rows:
        if args.quiet and state in ('OK',):
            continue
        lines.append(f"  {state:18}  {name:<{width}}  {msg}")

    if not problems:
        lines.append("")
        lines.append("All tokens healthy.")

    out = '\n'.join(lines)
    print(out)

    if args.telegram and problems:
        msg = f"<b>OAuth health: {len(problems)} issue(s)</b>\n" + '\n'.join(f"• {p}" for p in problems[:8])
        send_telegram(msg)

    return exit_code


if __name__ == '__main__':
    sys.exit(main())
