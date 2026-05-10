---
cadence: budget-review
role: ceo
frequency: monthly
duration: 30min
tools: [Read, Write, plur_recall_hybrid, datacore.search]
---

## Objective

Review the venture's budget burn rate against ceilings, flag any category over
budget, project runway, and surface unexpected expenses. Update the budget
ledger and capture follow-up tasks for any required reallocation.

## Steps

1. **Load budget config**: Read `venture.yaml` for `budget` block:
   - `ceiling` (monthly total)
   - `ai_tokens` (monthly AI spend ceiling)
   - `real_spend` (monthly out-of-pocket ceiling)

2. **Load ledger**: Read `[space]/.datacore/state/budget-ledger.yaml` (create
   if missing with empty schema). The ledger should have:
   - `period`: YYYY-MM
   - `entries`: list of `{date, category, amount, note, source}`
   - `aggregates`: `{ai_spent, real_spent, total_spent}` (rebuild from
     entries)

3. **Compute burn**: Sum entries for the current month. Compare against
   ceilings:
   - `ai_remaining` = `ai_tokens` − `ai_spent`
   - `real_remaining` = `real_spend` − `real_spent`
   - `total_remaining` = `ceiling` − `total_spent`
   - `days_remaining_in_month`
   - `burn_rate_per_day` = `total_spent` / `day_of_month`
   - `projected_eom` = `burn_rate_per_day` × `days_in_month`
   - `runway_days_at_current_burn` = `total_remaining` / `burn_rate_per_day`

4. **Flag overages and risks**:
   - Any category over its ceiling → CRITICAL
   - Any category projected to exceed ceiling by EOM → WARNING
   - Runway < 7 days → WARNING

5. **Identify unexpected expenses**: For each entry > 20% of monthly ceiling
   in a single line, surface for review. Tag with `:budget:review:` if it
   needs categorization.

6. **Recall prior burn patterns**: `plur_recall_hybrid` for venture name +
   "budget" — note recurring overages, seasonal patterns, prior reallocations.

7. **Write budget memo**: Compile via `datacore.capture` to the venture
   journal. Include burn table, projections, flags, unexpected-expense list.

8. **Auto-actions per policy**: Read
   `[space]/.datacore/policies/auto-defaults.yaml` for `budget_overage` rule.
   If AI budget is exhausted and policy says "downgrade to daily-only", set a
   marker file at `[space]/.datacore/state/budget-throttle.flag` that the
   cadence engine reads for filter_by_budget().

9. **Capture follow-ups**: For CRITICAL flags, create a `:CEO:` task in
   `org/inbox.org` with the specific reallocation decision needed.

## Output

- Updated `budget-ledger.yaml` (recomputed aggregates)
- Budget memo in venture journal
- Throttle flag file if budget exhausted (per policy)
- Follow-up tasks for CRITICAL overages

## Success Criteria

- Burn rate is quantified against ceiling, not narrated
- All categories are evaluated against their specific ceiling, not just total
- Runway projection uses current burn rate, not assumption
- Throttling auto-actions fire per policy without human intervention
- Critical flags produce a specific decision-needed task, not "review budget"
