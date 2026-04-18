---
cadence: content-calendar
role: cmo
frequency: weekly
duration: 30min
tools: [plur_recall_hybrid, datacore.search, Read, Glob]
---

## Objective

Plan the next week's content by reviewing recent output and mining internal sources for publishable material.

## Steps

1. **Load venture context**: Call `plur_recall_hybrid` with the venture name to retrieve brand voice, target audience, active channels, and content strategy.
2. **Review last week's content**: Use `Glob` to find recent files in `1-tracks/comms/` and check journal entries via `datacore.search` for content-related activity. Note what was published, what was skipped, and any engagement signals.
3. **Mine content sources**:
   - `Glob` for new files in `1-tracks/comms/drafts/` or `1-tracks/comms/ideas/`
   - Check `3-knowledge/literature/` for recent additions that could become thought-leadership posts
   - Check `3-knowledge/zettel/` for concepts that could become explainer threads
   - Use `Read` on recent GitHub releases or changelogs for product update content
4. **Check external triggers**: Use `plur_recall_hybrid` for any upcoming events, deadlines, or launches that need content support.
5. **Propose 3-5 content items**: For each item, specify:
   - **Title**: working headline
   - **Format**: thread, blog post, short-form, visual, newsletter
   - **Channel**: X, blog, GitHub, newsletter, community
   - **Day**: target publication day
   - **Source**: where the raw material lives
6. **Create tasks**: Add each content item as a TODO in `org/next_actions.org` with `:comms:content:` tags, scheduled for the target day. Include the source file path in the task body.
7. **Log to journal**: Call `datacore.capture` with the weekly content plan summary.

## Output

- 3-5 TODO tasks in `org/next_actions.org` with `:comms:content:` tags and scheduled dates
- Journal entry summarizing the content plan and rationale

## Success Criteria

- Content pipeline has no empty days in the coming week
- Each item has a clear source (not invented from nothing)
- Mix of formats and channels (not all the same type)
- Tasks are scheduled on specific days, not left undated
