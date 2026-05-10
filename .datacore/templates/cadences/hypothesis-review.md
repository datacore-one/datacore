---
cadence: hypothesis-review
role: ceo
frequency: monthly
duration: 45min
tools: [Read, Write, Edit, plur_recall_hybrid, datacore.search]
---

## Objective

Review every active hypothesis on the venture's hypothesis board: identify which
are validated, which are falsified, and which need a verdict. Apply the venture's
auto-default policy to falsified hypotheses if no human verdict has landed within
the SLA. **This cadence exists to prevent the H001-style 6-day decision-stall
pattern** — falsified hypotheses must produce a verdict (kill / redesign /
reframe) on every cycle.

## Steps

1. **Load hypothesis board**: Read `[space]/hypotheses.yaml` or
   `[space]/venture.yaml` (hypothesis section). Note each hypothesis's:
   - statement
   - gates (success/failure conditions with thresholds)
   - status (proposed / active / falsified / verified / paused / killed)
   - sample size / current data
   - filed date
   - last verdict date (if any)

2. **Load auto-default policy**: Read
   `[space]/.datacore/policies/auto-defaults.yaml`. Note the
   `falsified_hypothesis` section: SLA hours, default verdict, who to ping.

3. **Recall context**: `plur_recall_hybrid` for each active hypothesis ID +
   "verdict" / "falsified" — surface prior decisions and reasoning.

4. **Classify each hypothesis**:
   - **Active, on track** — sample size building, gates not yet hit either way
   - **Active, at sample target** — gates can now be evaluated; produce evidence
     summary
   - **Falsified, awaiting verdict** — gates failed, no verdict yet
   - **Falsified, past SLA** — apply auto-default per policy
   - **Verified, awaiting promotion** — gates passed, propose next-stage move
   - **Cold** — no movement in >30d, propose pause or kill

5. **For each falsified-past-SLA hypothesis**: Apply the auto-default verdict
   from policy. Update `hypotheses.yaml` status field. Write the verdict
   reasoning to the venture journal. Capture a follow-up task if the verdict
   is "redesign" or "reframe".

6. **For each falsified-awaiting-verdict hypothesis** (within SLA): File a
   pending-decision entry in `[space]/.datacore/state/decisions-pending.json`
   so it surfaces in the morning briefing and the datacore-app Hypothesis
   Board panel.

7. **For each verified-awaiting-promotion**: Draft the promotion proposal
   (next-stage hypothesis or scaling decision) and capture as a `:CEO:` task
   in `org/inbox.org`.

8. **Compare hypothesis genres**: Look at the prior month's content/output
   inventory. Which hypothesis genre produced the most signal? Surface the
   delta (e.g., "H002 = 6.6× reach of H001"). This drives the next cycle's
   resource reallocation.

9. **Write hypothesis memo**: Compile findings via `datacore.capture` to the
   venture journal. Include: status table, verdicts applied, pending-decision
   summary, genre-performance delta, recommendations for next cycle.

## Output

- Updated `hypotheses.yaml` with new statuses + verdict timestamps
- Pending-decision entries for the datacore-app surface
- Hypothesis memo in the venture journal
- Follow-up tasks in `org/inbox.org` for redesigns / reframes / promotions
- Cadence-log entry summarizing what fired

## Success Criteria

- Zero hypotheses sit "falsified, no verdict, past SLA" after this cadence runs.
  If the auto-default policy is missing or has no rule for a class, log a
  blocker and capture a CEO task to write the rule.
- Every falsified hypothesis has either a human verdict or an auto-default
  verdict, both timestamped and reasoned.
- Genre-performance delta is quantified, not narrated.
- Pending-decisions for in-SLA falsifications are surfaced to the human, not
  buried.
