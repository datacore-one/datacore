---
cadence: check-social-mentions
role: cmo
frequency: daily
duration: 15min
tools: [web_search_exa, plur_recall_hybrid, datacore.capture]
---

## Objective

Surface brand mentions from the past 24 hours and triage them into actionable categories.

## Steps

1. **Load venture context**: Call `plur_recall_hybrid` with the venture name to retrieve brand handles, product names, and key terminology to search for.
2. **Search X/Twitter mentions**: Use `web_search_exa` to search for `"{venture_name}"` OR `"@{twitter_handle}"` OR `"{product_name}"` filtered to the past 24 hours on twitter.com/x.com.
3. **Search broader web**: Use `web_search_exa` to search for the same terms across blogs, forums, Hacker News, and Reddit — past 24 hours.
4. **Classify each mention**: Assign one of three categories:
   - **ENGAGE** — positive mention, question, or feature request worth responding to
   - **WATCH** — neutral coverage or discussion, no action needed now
   - **ALERT** — negative sentiment, misinformation, competitor comparison, or security concern requiring escalation
5. **Draft replies for ENGAGE items**: Write concise, authentic replies (not corporate-speak). Reference specific details from the mention. Keep under 280 characters for X replies.
6. **Log to journal**: Call `datacore.capture` with a structured summary: date, total mentions found, breakdown by category, and drafted replies.
7. **Escalate ALERTs**: For any ALERT items, create a TODO task in `org/next_actions.org` under the Communications project with `:comms:urgent:` tags and link to the source.

## Output

- Journal entry with mention summary and category breakdown
- Draft replies for ENGAGE items (ready for human approval)
- org tasks for any ALERT escalations

## Success Criteria

- All brand mentions from past 24h are captured and classified
- ENGAGE replies are drafted within the same session
- ALERTs are escalated as tasks (not buried in a journal entry)
- Zero mentions are missed due to incomplete search terms
