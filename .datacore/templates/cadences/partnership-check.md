---
cadence: partnership-check
role: ceo
frequency: monthly
duration: 30min
tools: [Read, Glob, plur_recall_hybrid, datacore.search]
---

## Objective

Monthly partnership pipeline review — identify dormant contacts, follow up on opportunities, and maintain relationship health across the venture's network.

## Steps

1. **Load venture context**: Read `venture.yaml` to get the venture's space path, partnership goals, and any configured CRM references.

2. **Recall partnership context**: Call `plur_recall_hybrid` with "partnerships" and the venture name to retrieve:
   - Known partnership opportunities and their status
   - Key contacts and last interaction dates
   - Partnership criteria and deal-breakers
   - Past partnership outcomes (successful, failed, lessons learned)

3. **Scan CRM for dormant contacts**: Check CRM entries for contacts with no interaction in >30 days:
   ```
   Glob: {space}/3-knowledge/reference/*partner*
   Glob: {space}/3-knowledge/reference/*company*
   ```
   Also search journal entries:
   - Call `datacore.search` with "partnership" or "partner" and the venture name
   - Identify contacts mentioned but not followed up on

4. **Review partnership pipeline**: Check for partnership tracking files:
   ```
   Glob: {space}/1-tracks/ops/*partner*
   Glob: {space}/1-tracks/bizdev/*
   ```
   For each opportunity in the pipeline:
   - What stage is it in? (identified, contacted, in discussion, terms proposed, agreed, active)
   - When was the last touchpoint?
   - What is the next action?
   - Is it stalled? If so, why?

5. **Check for unrealized opportunities**: Look for partnership mentions in:
   - Journal entries from the past month
   - Meeting notes
   - Grant applications (co-applicant opportunities)
   - Community interactions (GitHub, social)
   Flag opportunities identified but not yet pursued.

6. **Assess active partnerships**: For existing active partnerships:
   - Is the partnership delivering expected value?
   - Are commitments being met on both sides?
   - Is there untapped potential in the relationship?
   - Any friction or misalignment emerging?

7. **Create follow-up tasks**: For each actionable item:
   - Dormant contacts worth re-engaging: org task with `:outreach:` tag
   - Stalled opportunities needing a nudge: org task with `:follow-up:` tag
   - New opportunities to explore: org task with `:bizdev:` tag
   - Active partnerships needing attention: org task with `:partnership:` tag

8. **Log partnership health**: Capture a partnership health summary via `datacore.capture`:
   - Pipeline: total opportunities, by stage, conversion rate
   - Active partnerships: count, health assessment
   - Dormant contacts: count, re-engagement priorities
   - Next month's partnership priorities

## Output

- Partnership pipeline status update
- Org tasks for follow-ups, outreach, and new opportunities
- Dormant contact list with re-engagement recommendations
- Active partnership health assessments
- Journal entry with pipeline metrics for trend tracking

## Success Criteria

- No valuable contact goes dormant >60 days without deliberate decision
- Every pipeline opportunity has a clear next action and owner
- Active partnerships are assessed for health (not assumed to be fine)
- New opportunities discovered in the past month are captured in pipeline
- Follow-up tasks are specific (who, what, why) not generic ("reach out to partners")
