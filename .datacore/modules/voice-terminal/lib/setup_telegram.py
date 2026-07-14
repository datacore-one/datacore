#!/usr/bin/env python3
"""
Telegram setup and validation for the voice-terminal module.

Run this to verify Telegram credentials are correctly configured for
speak_brief.py audio delivery. Used by the M4 install wizard.

Usage:
    python3 setup_telegram.py                  # Check credentials + send test message
    python3 setup_telegram.py --check-only     # Verify creds exist, no message sent
    python3 setup_telegram.py --write TOKEN CHAT_ID  # Write creds to nightshift.env
    python3 setup_telegram.py --show-config    # Show where creds are loaded from

The install wizard on the Mac app calls /daemon/test-telegram to do this remotely.
This script is for server-side setup and manual validation.
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path


DATA_DIR = Path(os.environ.get("DATA_DIR", Path.home() / "Data"))
ENV_DIR = DATA_DIR / ".datacore" / "env"
NIGHTSHIFT_ENV = Path.home() / "config" / "nightshift.env"


# ---- Credential Discovery (mirrors speak_brief._load_telegram_creds) ----

def discover_credentials() -> dict:
    """
    Scan all known credential locations and return a report.

    Returns:
        {
            "bot_token": str | None,
            "chat_id": str | None,
            "source": str,          # which file/env var supplied the values
            "env_sources": list,    # all files checked
        }
    """
    sources = []
    env_data = {}

    # 1. Environment variables (set by systemd EnvironmentFile or manual export)
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        env_data["TELEGRAM_BOT_TOKEN"] = os.environ["TELEGRAM_BOT_TOKEN"]
        env_data["_source_token"] = "env:TELEGRAM_BOT_TOKEN"
    if os.environ.get("TELEGRAM_CHAT_ID"):
        env_data["TELEGRAM_CHAT_ID"] = os.environ["TELEGRAM_CHAT_ID"]
        env_data["_source_chat"] = "env:TELEGRAM_CHAT_ID"

    # 2. Env files (same search order as speak_brief._load_telegram_creds)
    search_files = [
        ENV_DIR / ".env",
        ENV_DIR / "local.env",
        ENV_DIR / "mrdata.env",
        ENV_DIR / "gateio.env",      # legacy location
        NIGHTSHIFT_ENV,              # server config
    ]

    for env_file in search_files:
        sources.append(str(env_file))
        if not env_file.exists():
            continue
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k == "TELEGRAM_BOT_TOKEN" and "TELEGRAM_BOT_TOKEN" not in env_data:
                env_data["TELEGRAM_BOT_TOKEN"] = v
                env_data["_source_token"] = str(env_file)
            if k == "TELEGRAM_CHAT_ID" and "TELEGRAM_CHAT_ID" not in env_data:
                env_data["TELEGRAM_CHAT_ID"] = v
                env_data["_source_chat"] = str(env_file)

    token = env_data.get("TELEGRAM_BOT_TOKEN")
    chat_id = env_data.get("TELEGRAM_CHAT_ID")

    if token and chat_id:
        # Both found — determine canonical source
        t_src = env_data.get("_source_token", "?")
        c_src = env_data.get("_source_chat", "?")
        source = t_src if t_src == c_src else f"token:{t_src}, chat_id:{c_src}"
    elif token:
        source = f"token only ({env_data.get('_source_token', '?')}), CHAT_ID missing"
    elif chat_id:
        source = f"chat_id only ({env_data.get('_source_chat', '?')}), BOT_TOKEN missing"
    else:
        source = "not found"

    return {
        "bot_token": token,
        "chat_id": chat_id,
        "source": source,
        "env_sources_checked": sources,
    }


# ---- Validation ----

def validate_bot_token(token: str) -> dict:
    """Call Telegram getMe to validate the bot token."""
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        if data.get("ok"):
            bot = data["result"]
            return {
                "ok": True,
                "bot_name": bot.get("first_name"),
                "bot_username": bot.get("username"),
                "bot_id": bot.get("id"),
            }
        return {"ok": False, "error": str(data)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_test_message(token: str, chat_id: str) -> dict:
    """Send a test message to confirm the chat_id works."""
    try:
        text = (
            "Winston here. Telegram integration verified — "
            "your morning briefing audio will be delivered here."
        )
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        if result.get("ok"):
            return {"ok": True, "message_id": result["result"].get("message_id")}
        return {"ok": False, "error": str(result)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---- Credential Writing ----

def write_credentials(token: str, chat_id: str, env_file: Path = None) -> dict:
    """
    Write Telegram credentials to the nightshift env file.

    If the env file exists, upserts the two keys without disturbing other settings.
    If it doesn't exist, creates it.

    Default target: ~/config/nightshift.env (server config, loaded by systemd).
    """
    target = env_file or NIGHTSHIFT_ENV
    target.parent.mkdir(parents=True, exist_ok=True)

    existing_lines = []
    if target.exists():
        existing_lines = target.read_text().splitlines()

    # Upsert: remove existing TELEGRAM_* lines, append updated values
    new_lines = [
        line for line in existing_lines
        if not line.startswith("TELEGRAM_BOT_TOKEN=") and
           not line.startswith("TELEGRAM_CHAT_ID=")
    ]
    new_lines.append(f"TELEGRAM_BOT_TOKEN={token}")
    new_lines.append(f"TELEGRAM_CHAT_ID={chat_id}")

    try:
        target.write_text("\n".join(new_lines) + "\n")
        target.chmod(0o600)
        return {"ok": True, "written_to": str(target)}
    except OSError as e:
        return {"ok": False, "error": str(e)}


# ---- Output helpers ----

TICK = "[OK] "
CROSS = "[FAIL] "
WARN = "[WARN] "
INFO = "[INFO] "


def _mask(token: str) -> str:
    """Mask all but last 4 chars for display."""
    if not token or len(token) < 8:
        return "***"
    return "*" * (len(token) - 4) + token[-4:]


# ---- Main ----

def main():
    parser = argparse.ArgumentParser(
        description="Telegram setup and validation for voice-terminal"
    )
    parser.add_argument("--check-only", action="store_true",
                        help="Only verify credentials exist, do not send test message")
    parser.add_argument("--write", nargs=2, metavar=("TOKEN", "CHAT_ID"),
                        help="Write credentials to nightshift.env")
    parser.add_argument("--env-file", type=str,
                        help="Target env file for --write (default: ~/config/nightshift.env)")
    parser.add_argument("--show-config", action="store_true",
                        help="Show where credentials are loaded from and exit")
    args = parser.parse_args()

    print("=== Telegram Setup — voice-terminal / Winston audio briefing ===")
    print()

    # -- WRITE mode --
    if args.write:
        token, chat_id = args.write
        env_file = Path(args.env_file) if args.env_file else None
        print(f"Writing credentials to: {env_file or NIGHTSHIFT_ENV}")
        result = write_credentials(token, chat_id, env_file)
        if result["ok"]:
            print(f"{TICK}Written: {result['written_to']}")
        else:
            print(f"{CROSS}Write failed: {result['error']}")
            sys.exit(1)

        # Validate after write
        print()
        print("Validating written credentials...")
        bot_result = validate_bot_token(token)
        if bot_result["ok"]:
            print(f"{TICK}Bot valid: @{bot_result['bot_username']} ({bot_result['bot_name']})")
        else:
            print(f"{CROSS}Bot token invalid: {bot_result['error']}")
            sys.exit(1)

        print(f"Sending test message to chat_id {_mask(chat_id)}...")
        msg_result = send_test_message(token, chat_id)
        if msg_result["ok"]:
            print(f"{TICK}Test message delivered (message_id: {msg_result['message_id']})")
        else:
            print(f"{CROSS}Test message failed: {msg_result['error']}")
            print(f"{WARN}chat_id may be wrong — get it from: https://api.telegram.org/bot{_mask(token)}/getUpdates")
            sys.exit(1)
        print()
        print("Setup complete. speak_brief.py will deliver audio to Telegram on the next /today run.")
        return

    # -- SHOW CONFIG mode --
    creds = discover_credentials()
    if args.show_config:
        print("Credential search order:")
        for src in creds["env_sources_checked"]:
            exists = "[exists]" if Path(src).exists() else "[missing]"
            print(f"  {src} {exists}")
        print()
        if creds["bot_token"]:
            print(f"BOT_TOKEN  : {_mask(creds['bot_token'])} (from {creds['source']})")
        else:
            print(f"BOT_TOKEN  : NOT FOUND")
        if creds["chat_id"]:
            print(f"CHAT_ID    : {_mask(creds['chat_id'])} (from {creds['source']})")
        else:
            print(f"CHAT_ID    : NOT FOUND")
        return

    # -- CHECK / VALIDATE mode --
    print("Checking credential sources...")
    print(f"  Source: {creds['source']}")
    print()

    has_token = bool(creds["bot_token"])
    has_chat = bool(creds["chat_id"])

    if not has_token:
        print(f"{CROSS}TELEGRAM_BOT_TOKEN not found in any of:")
        for src in creds["env_sources_checked"]:
            print(f"   {src}")
        print()
        print("Fix: run  python3 setup_telegram.py --write YOUR_TOKEN YOUR_CHAT_ID")
        sys.exit(1)
    else:
        print(f"{TICK}BOT_TOKEN  : {_mask(creds['bot_token'])}")

    if not has_chat:
        print(f"{CROSS}TELEGRAM_CHAT_ID not found.")
        print("Fix: run  python3 setup_telegram.py --write YOUR_TOKEN YOUR_CHAT_ID")
        sys.exit(1)
    else:
        print(f"{TICK}CHAT_ID    : {_mask(creds['chat_id'])}")

    print()
    print("Validating bot token with Telegram API...")
    bot_result = validate_bot_token(creds["bot_token"])
    if bot_result["ok"]:
        print(f"{TICK}Bot valid  : @{bot_result['bot_username']} ({bot_result['bot_name']})")
    else:
        print(f"{CROSS}Bot invalid: {bot_result['error']}")
        sys.exit(1)

    if args.check_only:
        print()
        print("Check complete (--check-only, no test message sent).")
        return

    # Send test message
    print()
    print(f"Sending test message to chat_id {_mask(creds['chat_id'])}...")
    msg_result = send_test_message(creds["bot_token"], creds["chat_id"])
    if msg_result["ok"]:
        print(f"{TICK}Test message delivered! (message_id: {msg_result['message_id']})")
        print()
        print("All good. speak_brief.py will deliver audio to Telegram on the next /today run.")
    else:
        print(f"{CROSS}Test message failed: {msg_result['error']}")
        print()
        print("Common causes:")
        print("  - Bot never sent a message to this chat (Telegram won't accept)")
        print("    Fix: Open your bot in Telegram and send any message first")
        print(f"  - Wrong CHAT_ID — get it via: https://api.telegram.org/bot{_mask(creds['bot_token'])}/getUpdates")
        sys.exit(1)


if __name__ == "__main__":
    main()
