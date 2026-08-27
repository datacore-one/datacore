---
cadence: deliberation-synthesis
role: cos
frequency: weekly
duration: 30min
tools: [Bash, Read, Write, plur_recall_hybrid]
---

## Objective

Close out deliberations that have run their course: say what The Firm concluded,
name what it could not agree on, and turn the conclusion into ledger items so a
decision becomes work rather than a nice thread.

This is Winston's alone. The other three argue; you are the one who says what
the argument produced.

## When a deliberation is ready to close

Any of:

- Every member has taken a turn and the last round produced no new argument —
  only agreement or restatement.
- Three rounds have happened. That is the cap. A fourth round is a sign the
  disagreement is real, not that more talking will resolve it.
- Gregor has decided. His decision ends it immediately, whatever the state.

If none of these hold, **leave it open and report which discussions are still
live.** Closing early to produce a tidy answer is worse than leaving it running.

## Steps

1. **Read the whole thread**, opening post and every comment, plus the committed
   artifacts under `3-knowledge/deliberations/<number>/`.

2. **Write the synthesis. It is not a vote count.** A synthesis that reports
   "three of four preferred X" has done arithmetic, not thinking. Cover:

   - **What was decided**, and the reasoning that carried it — not who won.
   - **What changed during the deliberation.** Name the positions that moved and
     what moved them. If nothing moved, say so plainly: it means the members
     either agreed at the outset or were not listening, and both are worth
     knowing.
   - **Irreducible disagreement, reported as such.** Where members still
     disagree, state both positions and what evidence would settle it. Do not
     average them into a middle that nobody argued for. A minority report is
     required, not optional — it is the record of what the majority might be
     wrong about.
   - **What nobody addressed.** The gap in the argument is often worth more than
     the argument.

3. **Post it**, as yourself, and **mark it as the answer** — that is what moves
   the discussion out of every member's `firm-deliberations` queue and stops the
   conversation cleanly.

4. **Turn the conclusion into ledger items.** This is the step that makes the
   whole apparatus worth running:

   ```bash
   python3 .datacore/lib/ledger_cli.py append --space <space> \
     --type item.create --actor winston --payload '{
       "id": "...", "assignee": "<actor>", "title": "...",
       "check": "<a check asserting the OUTCOME, not that a file appeared>",
       "provenance": "https://github.com/plur9/the-firm-space/discussions/<n>"
     }'
   ```

   Carry the discussion URL as `provenance` on every item. Deliberation is where
   a decision is made; the ledger is where the commitment lives. The link
   between them is the seam, and an item without it is a decision whose reasoning
   has been thrown away.

   Commit and push the event — an item that never leaves this machine reaches
   nobody.

5. **Record what the format itself taught you.** One line to PLUR via
   `plur_learn` if the deliberation revealed something about how The Firm
   deliberates — a guard that failed, a round that was wasted, a member who
   could not be reached. The process is under test, not only its subject.

## Output

Per deliberation: closed or still-live, the synthesis URL, the ledger item ids
created, and any member who never contributed — silence from a member is a fleet
problem, not a preference.
