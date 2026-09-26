---
name: telegram-output-formatting
description: >
  How to format Telegram output for Gregor — short summaries with emoji titles,
  bullet-point lists, and full reports committed to plur-space. Applies to every
  report, briefing, task summary, and intelligence output delivered in Telegram DMs.
triggers:
  - reporting results in Telegram
  - summarizing task output
  - formatting lists or bullet points
  - delivering intelligence briefings
  - "format nicer"
  - "make it shorter"
version: 1.0.0
---

# Telegram Output Formatting

## The Rule

Telegram is the memo. The disk is the dossier.

Chat output = short, punchy, scannable.
Full reports = committed to the space's journal (`<space>/journal/`).

## Format Rules

### Titles
- Use emojis in section titles: ✅ 🔴 🟢 🟡 📋 🔍 💡 ⚠️
- Titles are SHORT — 2-4 words max
- CAPS for status markers when needed (OK, ERROR, DONE)

### Lists
- Use bullet points (•) — NOT hyphens (-)
- One line per item, no nested sub-bullets deeper than one level
- Keep items to a single sentence

### Length
- Telegram messages should fit on one phone screen
- Highlights and takeaways only — "what matters, what's next"
- If the output would exceed ~15 lines, write a full report to disk and
  summarize in chat with a pointer to the file

### Structure
A typical task report:

```
emoji TITLE

• Key finding 1
• Key finding 2
• Key finding 3

Next: what to do about it
```

For multi-item status reports (cron jobs, task lists):

```
📋 Header

🟢 Item 1 — status
🔴 Item 2 — status
🟢 Item 3 — status

Summary line
```

### What goes to disk, not chat
- Full methodology and sources
- Raw data dumps (GitHub API responses, scan logs)
- Multi-paragraph analysis
- Reproduction steps for technical issues
- Anything that would make the message scroll past one screen

Save full reports to: `<space>/journal/YYYY-MM-DD-topic.md`

## Group Chat vs DM

In The Firm group chat: even shorter. Phone screen, quick scan.
See `the-firm-etiquette` for when to respond at all.

In DM with Gregor: the rules above apply.

## Why

Gregor reads on a phone. The memo fits on one screen; the dossier
lives in the file. Intelligence briefings that dump full analysis
into chat are unreadable — the signal drowns in the noise.

## Cron Job Deliveries — Executive Summary Format

Cron job responses delivered to Telegram have a stricter format than interactive
responses. The user explicitly wants executive summaries, not log dumps.

**Rules for cron delivery format:**
- Title: "{Job Title} — {Human-friendly date}" (e.g., "Monday, September 22, 2026")
  — always include the day of the week, never ISO dates
- One-line TL;DR: what was done and the headline finding
- 2-5 bullet points: key findings ranked by importance
- One-line "What to watch" or "Action this week" closing note
- Word limits: 150 for daily jobs, 200 for weekly, 300 for monthly
- If nothing significant happened: respond with exactly `[SILENT]`

**MUST exclude from cron deliveries:**
- Job IDs, commit hashes, file paths, script names
- Raw API output, JSON, verification tables
- EventLog seq numbers, tool call counts
- Any technical implementation details

**Embedding the format in cron prompts:** Add a `## DELIVERY FORMAT (IMPORTANT)`
section to the end of each cron job prompt so the agent formats correctly without
needing to load a skill at runtime. See the `cron-delivery-formatting` skill for
the full template and before/after examples.

**Pitfall: cronjob update replaces the entire prompt.** When using
`cronjob(action='update')`, the new prompt replaces the old one entirely. Always
read the current prompt from `~/.hermes/cron/output/{job_id}/` output logs (between
`## Prompt` and `## Response` headers) before updating, and verify the
`prompt_preview` after.

A canonical version of the cron formatting standard is also committed to the-firm-space
repo at `1-tracks/operations/cron-delivery-formatting.md` for cross-agent visibility.

## Related Skills
- `the-firm-etiquette` — when to respond in group chat
- `venture-market-intelligence` — produces reports that follow this format
- `cron-delivery-formatting` — detailed cron-specific formatting standard with
  before/after examples, word limits by job type, and safe prompt-update pattern

## References
- `references/openrouter-credit-check.md` — How to check OpenRouter credit balance (python3 urllib workaround for blocklisted curl auth)
