---
cadence: campaign-retrospective
role: cmo
frequency: monthly
duration: 60min
tools: [plur_recall_hybrid, datacore.search, Read, Glob]
---

## Objective

Analyze the past month's communications performance to identify what worked, what didn't, and what to change next month.

## Steps

1. **Gather data sources**:
   - Use `datacore.search` to find all journal entries tagged with comms, content, outreach, or community from the past month
   - Use `Glob` to find campaign files in `1-tracks/comms/campaigns/` modified in the past month
   - Use `Glob` to check `1-tracks/comms/retro-*.md` for previous retrospectives (for trend comparison)
   - Call `plur_recall_hybrid` for any engagement metrics, social mention summaries, or content performance data stored in memory
2. **Inventory content output**: List every piece of content published (posts, threads, blog articles, newsletters, announcements). For each, note:
   - Format and channel
   - Topic and target audience
   - Engagement signals (if available from journal entries or mention checks)
3. **Analyze by channel**: Group content by channel (X, blog, GitHub, newsletter, community) and assess:
   - Volume: how many pieces per channel
   - Consistency: were there gaps or bursts
   - Engagement: which channels generated the most response
4. **Identify patterns**:
   - **Content types**: Which formats performed best (threads vs. announcements vs. deep dives)?
   - **Topics**: Which subject areas generated the most engagement?
   - **Timing**: Were there patterns in when content performed well?
   - **Outreach**: Which developer outreach efforts got responses?
5. **Compare to previous month**: If a previous retrospective exists (`1-tracks/comms/retro-*.md`), compare key metrics and trends. Note improvements and regressions.
6. **Write retrospective report**: Create `1-tracks/comms/retro-YYYY-MM.md` with:
   - **Summary**: 2-3 sentence overview of the month
   - **Content inventory**: table of all published content
   - **Channel analysis**: performance by channel
   - **Top performers**: best content pieces and why they worked
   - **Gaps**: missed opportunities or underperforming areas
   - **Patterns**: recurring themes from the data
   - **Recommendations**: 3-5 concrete action items for next month
7. **Create action items**: Add TODO tasks in `org/next_actions.org` with `:comms:strategy:` tags for each recommendation. Schedule them in the first week of the next month.
8. **Log to journal**: Call `datacore.capture` with a summary of the retrospective and key findings.

## Output

- Retrospective report at `1-tracks/comms/retro-YYYY-MM.md`
- 3-5 TODO tasks in `org/next_actions.org` with `:comms:strategy:` tags
- Journal entry with retrospective summary

## Success Criteria

- Every published content piece is accounted for in the inventory
- Analysis is data-driven (based on actual engagement signals, not assumptions)
- Recommendations are specific and actionable (not generic "post more")
- Retrospective builds on previous months (trend tracking, not isolated snapshots)
- Action items have specific deadlines and owners
