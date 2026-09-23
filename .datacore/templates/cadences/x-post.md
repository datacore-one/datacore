---
cadence: x-post
# DIP-0050: judged by today's post record -- the post text and its live URL,
# committed during the run. A post that never reached X leaves no URL and fails.
evidence:
  path: "1-tracks/comms/x/x-post-{date}.md"
  require: ["^# X post", "https://(x|twitter)\\.com/[A-Za-z0-9_]+/status/[0-9]+"]
  min_bytes: 100   # heading + post + URL; 2026-09-23 a real 184-byte record failed a 200 floor
role: cmo
frequency: daily
duration: 10min
tools: [Read, Write, exec]
---

## Objective

One short standalone post to @plur_ai a day, in the voice approved on 2026-09-22.

## Voice

Dry, recognizable observational humor about AI and memory through everyday human
situations. A stranger gets it without knowing PLUR. Short setup, understated twist, stop
at the punchline, no explanation afterward. Under 280 characters.

Never: sales copy, a lecture, a mandatory product mention or CTA, jargon, developer-only
or engram/scope insider jokes, forced punchlines, first-person narration, mentions of
profiles. Label Human/AI speakers in dialogue. Do not invent real incidents: invented
dialogue must read as a joke, not a testimonial.

Tone references only, never repost or paraphrase them:
1. An AI that remembers your preferences can stop asking annoying questions and start
   making disappointing assumptions. / Much more like a colleague.
2. Human: "Don't make that mistake again." / AI: "Of course. Would you like me to save
   that preference?"

## Steps

1. **Sync:** `git pull` in this space.
2. **Avoid repeats:** read the last 14 records in `1-tracks/comms/x/`; vary subject and structure.
3. **Draft, then critique:** would a stranger understand it, is the situation recognizable,
   can words be cut? Revise a weak draft; do not post it.
4. **Post:** with the workspace environment loaded (`set -a; . ~/Data/.datacore/env/.env; set +a`),
   `XPoster(account='plur')` from `~/Data/.datacore/modules/comms/lib/x_poster.py`.
5. **Record:** write `1-tracks/comms/x/x-post-YYYY-MM-DD.md` (date from
   `python3 ~/Data/.datacore/lib/date_utils.py today`), headed `# X post — YYYY-MM-DD`,
   with the post text and the URL X returned. Commit it; `git push`.
6. **If posting fails:** write no record. Say why in your reply; the runner reports the
   failed run. Do not log the run anywhere else.
