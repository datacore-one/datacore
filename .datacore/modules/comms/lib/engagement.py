"""Telegram callback handler for engagement approval buttons.

Handles inline button presses from engagement drafts.
Callback data format:
  eng:approve:{draft_id}      - Post via Tweepy immediately
  eng:edit:{draft_id}         - Send ForceReply asking for edited text
  eng:reject:{draft_id}       - Discard draft

Thread callback data format:
  thread:approve:{thread_id}  - Post thread chain immediately
  thread:edit:{thread_id}     - ForceReply: numbered lines to update tweets
  thread:skip:{thread_id}     - Mark thread as skipped

Edit flow (reply drafts):
  1. User taps Edit
  2. Bot sends ForceReply with draft text
  3. User replies with edited version
  4. handle_edit_reply() posts via Tweepy

Edit flow (threads):
  1. User taps Edit
  2. Bot sends ForceReply with numbered tweet list
  3. User replies with modified numbered lines
  4. handle_edit_reply() parses, updates thread, re-sends preview
"""

import os
import sys
import logging
from pathlib import Path

# Add comms lib to path for imports
COMMS_LIB = Path(__file__).parent.parent.parent / "comms" / "lib"
sys.path.insert(0, str(COMMS_LIB))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "lib"))

import engagement_state as state_mod
import engagement_notify as notify_mod
import engagement_post as post_mod
import engagement_draft as draft_mod

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# State file path
DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
STATE_FILE = DATA_DIR / ".datacore" / "state" / "engagement-state.json"
ENV_FILE = DATA_DIR / ".datacore" / "env" / ".env"

# Track pending edits: {bot_message_id: draft_id}
# In-memory only — edits expire when bot restarts (that's fine)
_pending_edits: dict[int, str] = {}

# Track pending thread edits: {bot_message_id: thread_id}
_pending_thread_edits: dict[int, str] = {}


def _ensure_env():
    """Load env vars if not already set."""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
                if key not in os.environ:
                    os.environ[key] = val


async def handle_engagement_callback(update, context):
    """Handle approve/edit/reject button presses for engagement drafts and threads."""
    query = update.callback_query
    data = query.data

    if data.startswith("thread:"):
        await handle_thread_callback(query, context)
        return

    if not data.startswith("eng:"):
        return

    await query.answer()

    parts = data.split(":", 2)
    if len(parts) != 3:
        await query.edit_message_text("Invalid callback data.")
        return

    action, draft_id = parts[1], parts[2]
    logger.info(f"Engagement callback: {action} for draft {draft_id}")

    _ensure_env()
    st = state_mod.load(STATE_FILE)
    draft = state_mod.get_pending(st, draft_id)

    if not draft:
        # Check if already processed (double-tap on old buttons)
        for p in st.get("posted", []):
            if p.get("id") == draft_id:
                await query.edit_message_text(
                    f"Already posted \u2713\n\n"
                    f"Reply to {notify_mod._escape_html(p.get('target_author', '?'))} "
                    f"was posted at {p.get('posted_at', '?')[:16]}.",
                    parse_mode="HTML",
                )
                return
        for r in st.get("rejected", []):
            if r.get("id") == draft_id:
                await query.edit_message_text(
                    f"Already rejected \u2717\n\n"
                    f"Draft for {notify_mod._escape_html(r.get('target_author', '?'))} "
                    f"was rejected at {r.get('rejected_at', '?')[:16]}.",
                    parse_mode="HTML",
                )
                return
        await query.edit_message_text(
            f"Draft {draft_id} expired or not found."
        )
        return

    if action == "approve":
        await _post_and_confirm(query, st, draft, draft_id, draft["draft_reply"])

    elif action == "edit":
        # Send the draft text back with ForceReply so user can edit it
        chat_id = query.message.chat_id
        prompt_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"Edit the reply to {notify_mod._escape_html(draft['target_author'])}.\n\n"
                f"Current draft:\n"
                f"<code>{notify_mod._escape_html(draft['draft_reply'])}</code>\n\n"
                f"Reply to this message with your edited version."
            ),
            parse_mode="HTML",
            reply_markup=ForceReply(selective=True),
        )
        # Track so handle_edit_reply knows which draft this is
        _pending_edits[prompt_msg.message_id] = draft_id
        logger.info(f"Sent edit prompt (msg {prompt_msg.message_id}) for draft {draft_id}")

    elif action == "redraft":
        await query.edit_message_text(
            f"<b>Redrafting...</b>\n\n"
            f"Generating a new reply to {notify_mod._escape_html(draft['target_author'])}",
            parse_mode="HTML",
        )
        try:
            conv = {
                "author": draft["target_author"],
                "content": draft.get("target_content_snippet", ""),
                "url": draft["target_url"],
            }
            new_draft = draft_mod.draft_reply(conv)
            # Update the pending item with new draft
            draft["draft_reply"] = new_draft
            state_mod.save(st, STATE_FILE)

            # Re-send with fresh buttons
            chat_id = query.message.chat_id
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"<b>Redrafted reply to {notify_mod._escape_html(draft['target_author'])}</b>\n\n"
                    f"<a href=\"{draft['target_url']}\">View original</a>\n\n"
                    f"<b>New draft:</b>\n"
                    f"{notify_mod._escape_html(new_draft)}\n\n"
                    f"<code>{len(new_draft)} chars</code>"
                ),
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✓ Approve", callback_data=f"eng:approve:{draft['id']}"),
                        InlineKeyboardButton("✎ Edit", callback_data=f"eng:edit:{draft['id']}"),
                    ],
                    [
                        InlineKeyboardButton("↺ Redraft", callback_data=f"eng:redraft:{draft['id']}"),
                        InlineKeyboardButton("✗ Reject", callback_data=f"eng:reject:{draft['id']}"),
                    ],
                ]),
            )
            logger.info(f"Redrafted {draft['id']} for {draft['target_author']}")
        except Exception as e:
            logger.error(f"Redraft failed for {draft['id']}: {e}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"Redraft failed: {notify_mod._escape_html(str(e))}",
                parse_mode="HTML",
            )

    elif action == "reject":
        state_mod.reject_pending(st, draft_id)
        state_mod.save(st, STATE_FILE)

        await query.edit_message_text(
            f"<b>Rejected</b>\n\n"
            f"<s>{notify_mod._escape_html(draft['draft_reply'])}</s>\n\n"
            f"Reply to {notify_mod._escape_html(draft['target_author'])} discarded.",
            parse_mode="HTML",
        )
        logger.info(f"Rejected draft {draft_id} for {draft['target_author']}")


async def handle_thread_callback(query, context):
    """Handle thread:approve / thread:edit / thread:skip callbacks."""
    await query.answer()
    data = query.data
    parts = data.split(":", 2)
    if len(parts) != 3:
        await query.edit_message_text("Invalid thread callback.")
        return

    action, thread_id = parts[1], parts[2]
    logger.info(f"Thread callback: {action} for {thread_id}")

    _ensure_env()

    import engagement_state as state_mod
    import today_thread as thread_mod

    st = state_mod.load(STATE_FILE)

    # Find thread in state
    thread = None
    for t in st.get("threads", []):
        if t.get("id") == thread_id:
            thread = t
            break

    if not thread:
        await query.edit_message_text(f"Thread {thread_id} not found.")
        return

    if action == "approve":
        await query.edit_message_text(
            f"<b>Posting thread...</b>",
            parse_mode="HTML",
        )
        try:
            thread_mod.post_thread(thread_id, st, STATE_FILE)
            # Reload state — post_thread saves updated posted_tweet_ids internally
            fresh_st = state_mod.load(STATE_FILE)
            thread = next((t for t in fresh_st.get("threads", []) if t.get("id") == thread_id), thread)
            tweets_posted = thread.get("posted_tweet_ids", [])
            first_url = f"https://x.com/FairDataSociety/status/{tweets_posted[0]}" if tweets_posted else "#"
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"<b>Thread posted ✓</b>\n\n"
                    f"{len(tweets_posted)} tweets\n"
                    f"<a href='{first_url}'>View thread</a>"
                ),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Thread post failed: {e}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"<b>Post failed:</b> {notify_mod._escape_html(str(e)[:200])}",
                parse_mode="HTML",
            )

    elif action == "skip":
        thread["status"] = "skipped"
        state_mod.save(st, STATE_FILE)
        await query.edit_message_text(
            f"<b>Thread skipped ✓</b>\n\n"
            f"Today's 'Today in Privacy' thread will not be posted.",
            parse_mode="HTML",
        )
        logger.info(f"Thread {thread_id} skipped")

    elif action == "edit":
        tweets = thread.get("tweets", [])
        numbered = "\n\n".join(f"{i+1}/ {t}" for i, t in enumerate(tweets))
        chat_id = query.message.chat_id
        prompt_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"Edit the thread. Reply with the full numbered list.\n\n"
                f"Current draft:\n\n"
                f"<code>{notify_mod._escape_html(numbered)}</code>\n\n"
                f"Reply to this message with your edited version."
            ),
            parse_mode="HTML",
            reply_markup=ForceReply(selective=True),
        )
        _pending_thread_edits[prompt_msg.message_id] = thread_id
        logger.info(f"Sent thread edit prompt for {thread_id}")


async def handle_edit_reply(update, context) -> bool:
    """Check if incoming message is a reply to an edit prompt. Post if so.

    Handles both draft edits and thread edits.
    Returns True if handled (message was an edit reply), False otherwise.
    """
    msg = update.message
    if not msg or not msg.reply_to_message:
        return False

    replied_to_id = msg.reply_to_message.message_id

    # Check if it's a thread edit reply
    thread_id = _pending_thread_edits.get(replied_to_id)
    if thread_id:
        del _pending_thread_edits[replied_to_id]
        await _handle_thread_edit(msg, context, thread_id)
        return True

    draft_id = _pending_edits.get(replied_to_id)
    if not draft_id:
        return False

    # Remove from pending edits (one-shot)
    del _pending_edits[replied_to_id]

    edited_text = msg.text.strip()
    if not edited_text:
        await msg.reply_text("Empty message — edit cancelled.")
        return True

    _ensure_env()
    st = state_mod.load(STATE_FILE)
    draft = state_mod.get_pending(st, draft_id)

    if not draft:
        await msg.reply_text(f"Draft {draft_id} expired or already processed.")
        return True

    # Post the edited version
    try:
        tweet_id = post_mod.post_reply(
            target_tweet_id=str(draft["target_tweet_id"]),
            reply_text=edited_text,
        )
        state_mod.approve_pending(st, draft_id, tweet_id)
        state_mod.save(st, STATE_FILE)

        tweet_url = f"https://x.com/FairDataSociety/status/{tweet_id}"
        await msg.reply_text(
            f"<b>Posted (edited)</b> ✓\n\n"
            f"<b>Reply to {notify_mod._escape_html(draft['target_author'])}:</b>\n"
            f"{notify_mod._escape_html(edited_text)}\n\n"
            f"<a href=\"{tweet_url}\">View posted reply</a>",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        logger.info(f"Posted edited reply {tweet_id} for draft {draft_id}")

    except Exception as e:
        logger.error(f"Failed to post edited reply for draft {draft_id}: {e}")
        await msg.reply_text(
            f"<b>Post failed</b>\n\n"
            f"Error: {notify_mod._escape_html(str(e))}",
            parse_mode="HTML",
        )

    return True


async def _handle_thread_edit(msg, context, thread_id: str):
    """Process thread edit reply: parse numbered lines, update state, re-send preview."""
    import re
    import engagement_state as state_mod
    import today_thread as thread_mod

    _ensure_env()
    edited_text = msg.text.strip()
    if not edited_text:
        await msg.reply_text("Empty message — thread edit cancelled.")
        return

    # Parse numbered tweet lines: "1/ tweet text" or "1. tweet text"
    tweets = re.split(r'\n+(?=\d+[/.])', edited_text)
    tweets = [re.sub(r'^\d+[/.]\s*', '', t.strip()) for t in tweets if t.strip()]

    if not tweets:
        await msg.reply_text("Could not parse numbered tweets. Edit cancelled.")
        return

    st = state_mod.load(STATE_FILE)
    thread = None
    for t in st.get("threads", []):
        if t.get("id") == thread_id:
            thread = t
            break

    if not thread:
        await msg.reply_text(f"Thread {thread_id} not found.")
        return

    thread["tweets"] = tweets
    thread["status"] = "pending"
    state_mod.save(st, STATE_FILE)

    # Re-send preview with updated tweets
    preview = "\n\n".join(f"<b>{i+1}/</b> {notify_mod._escape_html(t[:200])}"
                          for i, t in enumerate(tweets))
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    await context.bot.send_message(
        chat_id=msg.chat_id,
        text=(
            f"<b>Updated thread preview</b>\n\n"
            f"{preview}\n\n"
            f"<i>{len(tweets)} tweets · auto-posts at 09:00 UTC</i>"
        ),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✓ Post Now", callback_data=f"thread:approve:{thread_id}"),
            InlineKeyboardButton("✎ Edit", callback_data=f"thread:edit:{thread_id}"),
            InlineKeyboardButton("✗ Skip", callback_data=f"thread:skip:{thread_id}"),
        ]]),
    )
    logger.info(f"Updated thread {thread_id} with {len(tweets)} edited tweets")


async def _post_and_confirm(query, st, draft, draft_id, reply_text):
    """Approve draft — queue for Chrome-based posting.

    X Free tier API cannot reply to other users' tweets (403).
    Approved drafts are queued in state with status 'approved'.
    The Chrome feed engagement agent picks them up and posts via browser.
    """
    # Mark as approved in state (Chrome agent will post)
    state_mod.approve_pending(st, draft_id, our_tweet_id="pending_chrome")
    state_mod.save(st, STATE_FILE)

    # Also write to a simple queue file for the Chrome agent
    queue_file = DATA_DIR / ".datacore" / "state" / "reply-queue.jsonl"
    import json
    entry = {
        "draft_id": draft_id,
        "target_tweet_id": str(draft["target_tweet_id"]),
        "target_author": draft["target_author"],
        "target_url": draft.get("target_url", ""),
        "reply_text": reply_text,
        "approved_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
    }
    with open(queue_file, "a") as f:
        f.write(json.dumps(entry) + "\n")

    await query.edit_message_text(
        f"<b>Approved</b> ✓ (queued for posting)\n\n"
        f"<b>Reply to {notify_mod._escape_html(draft['target_author'])}:</b>\n"
        f"{notify_mod._escape_html(reply_text)}\n\n"
        f"<a href=\"{draft.get('target_url', '#')}\">View target tweet</a>\n\n"
        f"<i>Will be posted via browser by the engagement agent.</i>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    logger.info(f"Approved draft {draft_id} for Chrome posting to {draft['target_author']}")
