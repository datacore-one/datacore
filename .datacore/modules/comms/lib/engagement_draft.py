#!/usr/bin/env python3
"""Draft engagement replies using Claude Code in headless mode.

Runs `claude -p` with all examples inlined in the prompt.
No file reads needed — single-turn generation for speed and reliability.
No API key needed — uses the local Claude Code auth (OAuth).
"""

import json
import os
import subprocess
from pathlib import Path


DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))

PROMPT_TEMPLATE = """You are drafting a reply tweet for @FairDataSociety.

DO NOT read any files. All context is below. Just write the reply.

## Target tweet

Author: {author}
Tweet: {content}
URL: {url}

## CRITICAL: Reply Voice (NOT standalone post voice)

You are REPLYING to someone. This is a conversation, not a broadcast. Sound like a person — not a brand account.

**#1 RULE: Make the OP feel heard.** You're adding to their discussion, not teaching them.

**ANTI-SMUGNESS — these patterns get rejected:**
- "The step most miss:" / "Most people don't realize..." → condescending
- "X isn't a feature — it's the layer that..." → asserting authority over their framing
- "The real fix is..." / "The real X is..." → implies you know better than OP
- Starting every reply with "Exactly." or "Spot on." → too formulaic, sounds like a bot

**If mentioning Fairdrop/FDS:** only as a brief lived experience ("we hit this same wall building Fairdrop"), never as a recommendation or solution.

## Pick ONE reply type that fits the tweet:

1. **Simple agreement** — when their point is so good it stands alone. Just amplify it. Can be very short.
2. **Extension** — add one new angle they didn't mention. Short. Don't make it about FDS.
3. **Question** — genuine curiosity that extends the thread. Makes OP look like they sparked interesting discussion.
4. **Experience** — brief first-person from building privacy infrastructure. Grounded, specific, not preachy.

## Examples — notice the VARIETY in structure, length, and type

Good (different types):
- "This." (agreement — perfect when the point stands alone)
- "Policy changes. Architecture doesn't." (tight extension, no preamble)
- "What's the hardest part in practice — getting regulators to accept ZK proof for age-gating?" (question)
- "We hit the same wall building Fairdrop. Encrypt-before-upload changed what we could promise." (experience)
- "Nailed it. Where does the data live AFTER you encrypt it?" (extension)
- "And if the company gets acquired, that policy goes with it." (adds one concrete next step)
- "Exactly." (sometimes the shortest reply is the best one)

Bad (smug / formulaic / AI-sounding):
- "X isn't a feature — it's the layer that makes everything coherent." (asserting authority)
- "The step most miss: [insight]" (condescending framing)
- "Spot on. [long insight]" every single time (too predictable, sounds generated)
- "This is what happens when principles live in a press release instead of architecture." (great once, but don't reuse this structure)

## Rules

1. NO links
2. **1-2 SENTENCES MAX. Under 120 characters is ideal.** Absolute max 180.
3. No emojis. No hashtags. No marketing speak.
4. Don't mention FDS/Fairdrop unless it adds something specific and genuine.
5. ONE point. Not two. Not three.
6. **Vary your structure** — not every reply should start with an agreement word.

## Output

Return ONLY the reply text. Nothing else. No quotes, no explanation, no preamble.
Keep it SHORT. If you can say it in 8 words, don't use 20."""


def draft_reply(conversation: dict) -> str:
    """Generate a reply draft using Claude Code headless mode.

    All examples are inlined — no file reads needed.
    Should complete in 1 turn.

    Args:
        conversation: dict with author, content, url keys

    Returns:
        Draft reply text (under 280 chars)
    """
    prompt = PROMPT_TEMPLATE.format(
        author=conversation.get("author", "unknown"),
        content=conversation.get("content", ""),
        url=conversation.get("url", ""),
    )

    # Clean env: remove CLAUDECODE to allow nested invocation
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["HOME"] = os.environ.get("HOME", str(Path.home()))

    result = subprocess.run(
        [
            "claude", "-p",
            "--model", "sonnet",
            "--output-format", "text",
            "--no-session-persistence",
            "--max-turns", "2",
        ],
        input=prompt,
        capture_output=True,
        text=True,
        cwd=str(DATA_DIR),
        env=env,
        timeout=60,
    )

    if result.returncode != 0:
        raise Exception(f"Claude CLI failed: {result.stderr[:300]}")

    text = result.stdout.strip().strip('"').strip("'")

    # Enforce length
    if len(text) > 280:
        for i in range(279, 0, -1):
            if text[i] in ".!?":
                text = text[: i + 1]
                break
        else:
            text = text[:277] + "..."

    return text
