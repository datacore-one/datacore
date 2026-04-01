#!/usr/bin/env python3
"""Telegram callback handler for engagement draft approvals.

Polls Telegram for callback_query updates (inline button presses).
Processes: approve → post via X API, reject → discard, redraft → re-queue.

Run as a long-lived process (systemd service) or periodic cron job.

Usage:
    python3 engagement_callback.py              # poll once
    python3 engagement_callback.py --daemon      # continuous polling (30s interval)
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

LIB_DIR = Path(__file__).parent
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(LIB_DIR.parent.parent.parent / "lib"))

DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
STATE_FILE = DATA_DIR / ".datacore" / "state" / "engagement-state.json"
OFFSET_FILE = DATA_DIR / ".datacore" / "state" / "telegram-callback-offset"
VOICE_FILE = DATA_DIR / ".datacore" / "state" / "voice-corrections.jsonl"
EDIT_PENDING_FILE = DATA_DIR / ".datacore" / "state" / "edit-pending.json"

POLL_INTERVAL = 30  # seconds between polls in daemon mode


def load_env():
    """Load env vars from .env files."""
    try:
        from env_utils import load_env_files
        load_env_files()
    except Exception:
        pass


def _get_offset() -> int:
    """Read the last processed update_id."""
    if OFFSET_FILE.exists():
        try:
            return int(OFFSET_FILE.read_text().strip())
        except (ValueError, OSError):
            pass
    return 0


def _set_offset(offset: int):
    """Persist the latest update_id so we don't re-process."""
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(offset))


def poll_callbacks() -> list:
    """Poll Telegram for new callback_query updates.

    Returns list of callback_query objects.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN not set")
        return []

    offset = _get_offset()
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {
        "offset": offset + 1 if offset else 0,
        "timeout": 0,  # Short poll (no long polling — avoids 409 with other consumers)
        "allowed_updates": ["callback_query", "message"],
    }

    body = json.dumps(params).encode()
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
    except HTTPError as e:
        if e.code == 409:
            # Another process is polling (e.g., n8n). Use short poll retry.
            # This is expected — we'll catch updates on next cycle.
            return []
        print(f"Telegram poll error: {e}")
        return []
    except Exception as e:
        print(f"Telegram poll error: {e}")
        return []

    if not result.get("ok"):
        return []

    updates = result.get("result", [])
    callbacks = []

    for update in updates:
        update_id = update.get("update_id", 0)
        if update_id > offset:
            _set_offset(update_id)

        cb = update.get("callback_query")
        if cb:
            callbacks.append({"type": "callback", "data": cb})
            _answer_callback(token, cb["id"])

        msg = update.get("message")
        if msg and msg.get("reply_to_message"):
            callbacks.append({"type": "reply", "data": msg})

    return callbacks


def _answer_callback(token: str, callback_query_id: str, text: str = "Processing..."):
    """Acknowledge callback to Telegram (dismisses loading indicator)."""
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    body = json.dumps({"callback_query_id": callback_query_id, "text": text}).encode()
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=5):
            pass
    except Exception:
        pass


def process_callback(callback_data: str, message_id: int) -> str:
    """Process a single callback action.

    Callback data format: eng:{action}:{draft_id}
    Actions: approve, reject, redraft, edit

    Returns: status string
    """
    parts = callback_data.split(":")
    if len(parts) != 3 or parts[0] != "eng":
        return f"unknown callback format: {callback_data}"

    action = parts[1]
    draft_id = parts[2]

    import engagement_state as state_mod
    import engagement_notify as notify_mod

    st, baseline = state_mod.load(STATE_FILE)
    pending = state_mod.get_pending(st, draft_id)

    if not pending:
        _update_telegram_message(message_id, f"Draft {draft_id} not found (expired or already processed)")
        return f"draft {draft_id} not found"

    if action == "approve":
        return _handle_approve(st, baseline, pending, draft_id, message_id)
    elif action == "reject":
        return _handle_reject(st, baseline, draft_id, message_id)
    elif action == "redraft":
        return _handle_redraft(st, baseline, pending, draft_id, message_id)
    elif action == "edit":
        return _handle_edit(st, baseline, pending, draft_id, message_id)
    else:
        return f"unknown action: {action}"


def _handle_approve(st: dict, baseline: dict, pending: dict, draft_id: str, message_id: int) -> str:
    """Approve a draft: post via X API, update state, update Telegram message."""
    import engagement_state as state_mod

    target_tweet_id = pending.get("target_tweet_id", "")
    draft_reply = pending.get("draft_reply", "")
    target_author = pending.get("target_author", "")

    # Post via X API — use account from pending draft (defaults to 'fds')
    account = pending.get("account", "fds")
    our_tweet_id = None
    try:
        from x_poster import XPoster
        poster = XPoster(account=account)
        result = poster.reply(text=draft_reply, reply_to_id=target_tweet_id)
        our_tweet_id = result.get("data", {}).get("id", "unknown")
        print(f"  Posted reply {our_tweet_id} to {target_author} (account={account})")
    except Exception as e:
        err = str(e)
        print(f"  Post failed: {err}")
        _update_telegram_message(message_id, f"Error: {err[:200]}\n\nDraft preserved — try again or reject.")
        return f"post failed: {err[:100]}"

    # Update state
    state_mod.approve_pending(st, draft_id, our_tweet_id or "unknown")
    state_mod.save(st, STATE_FILE, baseline=baseline)

    # Log voice signal — approved drafts = "more like this"
    _log_voice_signal(
        account=account, signal_type="approve", original=draft_reply,
        target_author=target_author, context=pending.get("target_content_snippet", ""),
    )

    # Update Telegram message — use account handle for URL
    ACCOUNT_HANDLES = {'fds': 'FairDataSociety', 'plur': 'plur_ai', 'datacore': 'datacore', 'mr_data_dc': 'mr_data_dc'}
    handle = ACCOUNT_HANDLES.get(account, account)
    reply_url = f"https://x.com/{handle}/status/{our_tweet_id}" if our_tweet_id else ""
    _update_telegram_message(
        message_id,
        f"POSTED to {_escape_html(target_author)}\n\n"
        f"{_escape_html(draft_reply)}\n\n"
        f"<a href='{reply_url}'>View reply</a>"
    )

    return f"approved and posted: {our_tweet_id}"


def _handle_reject(st: dict, baseline: dict, draft_id: str, message_id: int) -> str:
    """Reject a draft: remove from pending, update Telegram message."""
    import engagement_state as state_mod

    item = state_mod.reject_pending(st, draft_id)
    state_mod.save(st, STATE_FILE, baseline=baseline)

    if item:
        # Log voice signal — rejected drafts = "not like this"
        _log_voice_signal(
            account=item.get("account", "fds"), signal_type="reject",
            original=item.get("draft_reply", ""),
            target_author=item.get("target_author", ""),
            context=item.get("target_content_snippet", ""),
        )
        _update_telegram_message(message_id, f"REJECTED: {_escape_html(item.get('draft_reply', '')[:100])}")
    return f"rejected: {draft_id}"


def _handle_redraft(st: dict, baseline: dict, pending: dict, draft_id: str, message_id: int) -> str:
    """Redraft: generate a new reply for the same target, replace pending entry."""
    import engagement_state as state_mod
    import engagement_draft as draft_mod

    target_author = pending.get("target_author", "")
    target_content = pending.get("target_content_snippet", "")
    target_url = pending.get("target_url", "")

    # Generate new draft
    try:
        conv = {
            "author": target_author,
            "content": target_content,
            "url": target_url,
        }
        new_draft = draft_mod.draft_reply(conv)
        print(f"  Redrafted for {target_author}: {new_draft[:80]}...")
    except Exception as e:
        _update_telegram_message(message_id, f"Redraft failed: {str(e)[:200]}")
        return f"redraft failed: {e}"

    # Update the pending entry with new draft
    pending["draft_reply"] = new_draft

    state_mod.save(st, STATE_FILE, baseline=baseline)

    # Send new draft to Telegram (new message, update old one)
    _update_telegram_message(message_id, f"REDRAFTED (see new message below)")

    try:
        import engagement_notify as notify_mod
        notify_mod.send_draft_for_approval(
            draft_id=draft_id,
            target_author=target_author,
            target_content=target_content,
            target_url=target_url,
            draft_reply=new_draft,
            auto_register=False,  # Already in state
        )
    except Exception as e:
        print(f"  Telegram re-send failed: {e}")

    return f"redrafted: {draft_id}"


def _handle_edit(st: dict, baseline: dict, pending: dict, draft_id: str, message_id: int) -> str:
    """Edit mode: prompt user to reply with corrected text."""
    _set_edit_pending(draft_id, message_id, pending)
    _update_telegram_message(
        message_id,
        f"✎ EDIT MODE\n\n"
        f"Reply to this message with your corrected text.\n"
        f"It will be posted and the correction stored for voice training.\n\n"
        f"Original: {_escape_html(pending.get('draft_reply', ''))}\n"
        f"Target: {_escape_html(pending.get('target_author', ''))}"
    )
    return f"edit mode activated for {draft_id}"


def _handle_edit_reply(text: str, reply_to_message_id: int) -> str:
    """Process a user's edited reply text (sent as reply to edit-mode message)."""
    import engagement_state as state_mod

    edit_state = _get_edit_pending()
    edit_info = edit_state.get(str(reply_to_message_id))
    if not edit_info:
        return "no edit pending for this message"

    draft_id = edit_info["draft_id"]
    original = edit_info["original_draft"]
    account = edit_info.get("account", "fds")
    target_tweet_id = edit_info["target_tweet_id"]
    target_author = edit_info["target_author"]

    # Post the corrected version
    our_tweet_id = None
    try:
        from x_poster import XPoster
        poster = XPoster(account=account)
        result = poster.reply(text=text, reply_to_id=target_tweet_id)
        our_tweet_id = result.get("data", {}).get("id", "unknown")
        print(f"  Posted edited reply {our_tweet_id} to {target_author} (account={account})")
    except Exception as e:
        err = str(e)
        print(f"  Edit-post failed: {err}")
        _send_telegram_message(f"Edit post FAILED: {err[:200]}\n\nYour text preserved, try posting manually.")
        return f"edit post failed: {err[:100]}"

    # Update engagement state
    st, baseline = state_mod.load(STATE_FILE)
    state_mod.approve_pending(st, draft_id, our_tweet_id or "unknown")
    state_mod.save(st, STATE_FILE, baseline=baseline)

    # Log voice correction (the key training signal)
    _log_voice_signal(
        account=account,
        signal_type="edit",
        original=original,
        corrected=text,
        target_author=target_author,
        context=edit_info.get("target_content", ""),
    )

    # Clean up edit state
    _clear_edit_pending(reply_to_message_id)

    # Confirm in Telegram
    ACCOUNT_HANDLES = {'fds': 'FairDataSociety', 'plur': 'plur_ai', 'datacore': 'datacore', 'mr_data_dc': 'mr_data_dc'}
    handle = ACCOUNT_HANDLES.get(account, account)
    reply_url = f"https://x.com/{handle}/status/{our_tweet_id}" if our_tweet_id else ""
    _send_telegram_message(
        f"POSTED (edited) to {_escape_html(target_author)}\n\n"
        f"{_escape_html(text)}\n\n"
        f"Voice correction saved ✓\n"
        f"<a href='{reply_url}'>View reply</a>"
    )

    return f"edited and posted: {our_tweet_id}"


def _send_telegram_message(text: str):
    """Send a new Telegram message (not edit)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"  Telegram send failed: {e}")


def _update_telegram_message(message_id: int, text: str):
    """Edit a Telegram message to show the action result."""
    try:
        import engagement_notify as notify_mod
        notify_mod.edit_message(message_id, text)
    except Exception as e:
        print(f"  Telegram edit failed: {e}")


def _log_voice_signal(account: str, signal_type: str, original: str, corrected: str = None,
                      target_author: str = "", context: str = ""):
    """Log a voice training signal to JSONL file.

    signal_type: 'approve' | 'reject' | 'edit' (original→corrected pair)
    """
    VOICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "account": account,
        "signal": signal_type,
        "original": original,
        "corrected": corrected,
        "target_author": target_author,
        "context": context[:200] if context else "",
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with open(VOICE_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _set_edit_pending(draft_id: str, message_id: int, pending: dict):
    """Store edit-pending state so we can match the user's reply message."""
    EDIT_PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = {}
    if EDIT_PENDING_FILE.exists():
        try:
            state = json.loads(EDIT_PENDING_FILE.read_text())
        except Exception:
            pass
    state[str(message_id)] = {
        "draft_id": draft_id,
        "original_draft": pending.get("draft_reply", ""),
        "target_tweet_id": pending.get("target_tweet_id", ""),
        "target_author": pending.get("target_author", ""),
        "target_content": pending.get("target_content_snippet", ""),
        "target_url": pending.get("target_url", ""),
        "account": pending.get("account", "fds"),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    EDIT_PENDING_FILE.write_text(json.dumps(state, indent=2))


def _get_edit_pending() -> dict:
    """Load all edit-pending entries."""
    if EDIT_PENDING_FILE.exists():
        try:
            return json.loads(EDIT_PENDING_FILE.read_text())
        except Exception:
            pass
    return {}


def _clear_edit_pending(message_id: str):
    """Remove a processed edit-pending entry."""
    state = _get_edit_pending()
    state.pop(str(message_id), None)
    EDIT_PENDING_FILE.write_text(json.dumps(state, indent=2))


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def run_once():
    """Poll once, process all pending callbacks."""
    callbacks = poll_callbacks()
    if not callbacks:
        return 0

    processed = 0
    for item in callbacks:
        if item["type"] == "callback":
            cb = item["data"]
            data = cb.get("data", "")
            msg = cb.get("message", {})
            message_id = msg.get("message_id", 0)

            if not data.startswith("eng:"):
                continue

            print(f"Processing callback: {data} (msg {message_id})")
            status = process_callback(data, message_id)
            print(f"  Result: {status}")
            processed += 1

        elif item["type"] == "reply":
            msg = item["data"]
            text = msg.get("text", "").strip()
            reply_to = msg.get("reply_to_message", {})
            reply_to_id = reply_to.get("message_id", 0)

            if not text or not reply_to_id:
                continue

            # Check if this is a reply to an edit-mode message
            edit_state = _get_edit_pending()
            if str(reply_to_id) in edit_state:
                print(f"Processing edit reply to msg {reply_to_id}: {text[:60]}...")
                status = _handle_edit_reply(text, reply_to_id)
                print(f"  Result: {status}")
                processed += 1

    return processed


def run_daemon():
    """Continuous polling loop."""
    print(f"Engagement callback handler starting (polling every {POLL_INTERVAL}s)...")
    while True:
        try:
            n = run_once()
            if n > 0:
                print(f"Processed {n} callbacks")
        except KeyboardInterrupt:
            print("Shutting down...")
            break
        except Exception as e:
            print(f"Error in poll cycle: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    load_env()
    if "--daemon" in sys.argv:
        run_daemon()
    else:
        n = run_once()
        print(f"Processed {n} callbacks")
