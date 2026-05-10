---
cadence: listing-pipeline-review
role: operator
frequency: weekly
duration: 30min
tools: [Read, Write, plur_recall_hybrid, datacore.search]
---

## Objective

Weekly review of the Forge listing pipeline — what's queued, what's drafted,
what's blocked, what shipped this week. Surface bottlenecks and decide
priorities for the coming week.

## Steps

1. **Load pipeline state**: Read `4-forge/1-tracks/dashboard.md` and
   `4-forge/1-tracks/product/_index.md` (or product directory listing).
   Categorize each product:
   - **LIVE** — published on Etsy, generating views
   - **READY** — assets complete, awaiting publish
   - **DRAFT** — in active production
   - **QUEUED** — designed but not started
   - **BLOCKED** — waiting on dependency
   - **RETIRED** — pulled from sale

2. **Pull this week's movements**: Search journal for the past 7 days.
   Identify:
   - Products that advanced status (e.g., DRAFT → READY)
   - Products that stalled (no movement in 14+ days)
   - New products added to QUEUED

3. **Recall prior bottlenecks**: `plur_recall_hybrid` for "Forge pipeline" +
   "blocked" — note recurring blockers (mockup time, copy iteration, image
   approval).

4. **For each BLOCKED product**: Identify the specific blocker. If unresolved
   for >14d, escalate to `:CEO:forge:` task with the resolution question.

5. **For each STALLED-DRAFT** (>14d no movement): Same — escalate or kill.

6. **Compute pipeline health**:
   - count by status
   - average days in each status
   - throughput (READY/week)
   - WIP limits (recommended: ≤3 DRAFT, ≤2 READY)

7. **Decide next week's priorities**: Pick the top 3 actions for the coming
   week. Examples: ship 2 READY products, unblock #X by doing Y, kill #Z.
   Capture as `:operator:forge:` tasks with explicit deliverables.

8. **Write pipeline memo**: Via `datacore.capture` — status counts, this
   week's movements, blockers identified, next week's priorities.

## Output

- Pipeline memo in 4-forge journal
- Escalation tasks for stuck items
- Next-week priority tasks (top 3)
- Updated dashboard with status counts

## Success Criteria

- Every product is in one of the 6 status buckets — no "not sure"
- WIP exceeds limits → explicit decision to defer or de-prioritize
- Stuck items produce decision tasks, not "still blocked" notes
- Top 3 priorities are deliverables ("ship #4"), not activities ("work on #4")
