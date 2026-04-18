---
cadence: strategy-review
role: ceo
frequency: weekly
duration: 30min
tools: [Read, plur_recall_hybrid, datacore.search]
---

## Objective

Weekly strategic assessment — evaluate venture progress against thesis, review hypotheses, check budget, and set priorities for the coming week.

## Steps

1. **Load venture context**: Read `venture.yaml` to get the venture's thesis, north_star metric, current stage, hypotheses file path, and budget file path.

2. **Review the week's accomplishments**: Search the journal for this week's entries:
   - Call `datacore.search` with the venture name and date range for the past 7 days
   - Read relevant journal entries to understand what was achieved
   - Note completed milestones, shipped features, closed deals, or validated hypotheses

3. **Recall strategic context**: Call `plur_recall_hybrid` with the venture name and "strategy" to retrieve past strategic decisions, pivot points, and rationale.

4. **Check hypothesis board**: Read `hypotheses.yaml` (path from venture.yaml):
   - Identify hypotheses approaching their deadline
   - Check validation status: which have evidence for/against?
   - Flag any hypothesis past deadline without a decision (validate/invalidate/extend)
   - Note hypotheses that need new experiments designed

5. **Review budget**: Read `budget-ledger.yaml` (path from venture.yaml):
   - Check burn rate vs. budget
   - Flag any categories over budget
   - Project runway at current spend rate
   - Note any unexpected expenses

6. **Assess on-track / off-track**: Against the north_star metric:
   - Is the venture making measurable progress toward the north star?
   - Are current activities aligned with the thesis?
   - Are there signs of thesis drift (doing work that doesn't connect to the north star)?

7. **Identify blockers**: List anything impeding progress:
   - Resource constraints (time, money, people)
   - Technical blockers
   - External dependencies (partners, approvals, market conditions)
   - Decision debt (deferred decisions that are now urgent)

8. **Set top 3 priorities for next week**: Based on the assessment:
   - What are the 3 most important things to accomplish?
   - Do any priorities need to shift based on new information?
   - Create org tasks for priorities that don't already have them

9. **Write strategy memo**: Compile findings into a concise strategy update. Capture in journal via `datacore.capture`.

## Output

- Strategy memo in journal: week summary, on-track assessment, hypothesis status, budget snapshot, blockers, next week priorities
- Org tasks for top 3 priorities (if not already existing)
- Hypothesis board updates if any deadlines passed
- Budget alerts if any categories are over budget

## Success Criteria

- North star metric progress is quantified (not just "good" or "bad")
- All hypotheses past deadline have a decision or extension logged
- Budget review catches overspend before it becomes critical
- Top 3 priorities are specific and actionable (not vague aspirations)
- Strategy memo is written and captured in journal
