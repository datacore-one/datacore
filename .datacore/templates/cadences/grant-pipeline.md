---
cadence: grant-pipeline
role: ceo
frequency: weekly
duration: 20min
tools: [Read, Glob, plur_recall_hybrid]
---

## Objective

Monitor active grant applications and deadlines — ensure no submission window is missed and pipeline status stays current.

## Steps

1. **Load venture context**: Read `venture.yaml` to get the venture's space path and any grant-related configuration.

2. **Scan grant pipeline**: Look for grant tracking files in the ops track:
   ```
   Glob: {space}/1-tracks/ops/grants*
   Glob: {space}/1-tracks/ops/*grant*
   Glob: {space}/1-tracks/ops/*funding*
   ```
   Read any grant pipeline, tracker, or status files found.

3. **Recall grant history**: Call `plur_recall_hybrid` with "grants" and the venture name to retrieve:
   - Past applications and their outcomes
   - Known funding programs relevant to the venture
   - Contact information for program officers
   - Lessons learned from previous submissions

4. **Check active applications**: For each active grant application:
   - What stage is it in? (draft, submitted, under review, awarded, rejected)
   - When was it submitted?
   - What is the expected decision timeline?
   - Are there any follow-up actions required (additional documentation, interviews)?

5. **Flag approaching deadlines**: Identify deadlines within the next 14 days:
   - Submission deadlines for new applications
   - Reporting deadlines for awarded grants
   - Milestone delivery dates
   - Create org tasks with appropriate urgency:
     - Within 7 days: `:urgent:` tag
     - Within 14 days: `:deadline:` tag

6. **Update pipeline status**: For each grant in the pipeline:
   - Update status if new information is available
   - Move completed/rejected grants to archive
   - Note any status changes since last review

7. **Note new opportunities**: Check if any new funding opportunities have been discovered:
   - Read recent journal entries for mentions of grants or funding
   - Check `3-knowledge/` for newly added funding-related references
   - If promising opportunities exist without applications started, create org tasks to begin drafting

8. **Log pipeline summary**: Record current pipeline state: active applications count, pending deadlines, total funding in pipeline, success rate.

## Output

- Updated pipeline status for all active grants
- Org tasks with `:urgent:` or `:deadline:` tags for approaching deadlines
- New opportunity tasks if undiscovered funding sources found
- Pipeline summary: active count, pending value, next deadline, win rate

## Success Criteria

- No grant deadline is missed (all deadlines within 14 days have org tasks)
- Pipeline status reflects reality (no stale entries)
- Every active application has a clear next action
- New opportunities are captured, not lost
