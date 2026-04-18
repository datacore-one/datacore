---
cadence: positioning-review
role: ceo
frequency: monthly
duration: 45min
tools: [Read, web_search_exa, plur_recall_hybrid, datacore.search]
---

## Objective

Monthly positioning check — verify messaging accuracy, scan competitor landscape, and assess whether the venture's thesis and target customer definition still hold.

## Steps

1. **Load venture context**: Read `venture.yaml` to get the venture's thesis, target_customer, positioning statement, and competitor references.

2. **Recall positioning history**: Call `plur_recall_hybrid` with "positioning" and the venture name to retrieve:
   - Previous positioning decisions and rationale
   - Known competitors and differentiation points
   - Past messaging pivots and why they happened

3. **Review current messaging**: Read the venture's public-facing content:
   - README files in main repos
   - Landing page copy if available in the codebase
   - Marketing materials in `1-tracks/marketing/` or `1-tracks/comms/`
   - Verify messaging aligns with current thesis and north star

4. **Scan competitor landscape**: Read existing competitor analysis in `3-knowledge/reference/`:
   ```
   Glob: {space}/3-knowledge/reference/*competitor*
   Glob: {space}/3-knowledge/reference/*landscape*
   ```
   Check for recent changes or new entries.

5. **Search for competitor movements**: Use web search to check for recent competitor activity:
   - Call `web_search_exa` with competitor names and the venture's market category
   - Look for: new product launches, funding announcements, pivots, pricing changes, acquisitions
   - Note anything that changes the competitive dynamic

6. **Validate thesis**: Re-read the thesis from `venture.yaml`:
   - Is the problem statement still accurate?
   - Has the market shifted in a way that strengthens or weakens the thesis?
   - Are there new tailwinds or headwinds?
   - Does the evidence from the past month support or contradict the thesis?

7. **Validate target customer**: Review the `target_customer` definition:
   - Are the actual users/customers matching the target profile?
   - Is there a segment showing unexpected traction (potential pivot signal)?
   - Are there segments being ignored that should be targeted?

8. **Assess differentiation**: Is the venture's unique value proposition still:
   - True (actually differentiated)?
   - Relevant (customers care about this difference)?
   - Defensible (competitors can't easily copy)?

9. **Write positioning memo**: Compile findings into a monthly positioning memo:
   - Current positioning assessment (on-point / needs adjustment / needs overhaul)
   - Competitor landscape changes
   - Thesis validation status
   - Recommended adjustments (if any)
   - Capture via `datacore.capture` as a knowledge note

10. **Update venture.yaml if needed**: If the thesis, target_customer, or positioning needs updating, draft proposed changes and create an org task for human review.

## Output

- Positioning memo captured as knowledge note
- Competitor landscape update with any new movements
- Thesis validation assessment: confirmed / challenged / needs revision
- Target customer validation: accurate / needs refinement
- Org task if venture.yaml updates are recommended

## Success Criteria

- Messaging is checked against actual thesis (not assumed to be correct)
- At least 3 competitor signals are checked via web search
- Thesis assessment is evidence-based (cites specific data points)
- Target customer definition is validated against actual traction data
- Any recommended changes have specific, actionable proposals
