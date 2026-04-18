---
cadence: developer-outreach
role: cmo
frequency: weekly
duration: 30min
tools: [web_search_exa, plur_recall_hybrid, datacore.capture]
---

## Objective

Find developers experiencing problems the venture solves and draft genuinely helpful outreach — no spam, only value-first engagement.

## Steps

1. **Load venture context**: Call `plur_recall_hybrid` with the venture name to retrieve value proposition, target developer personas, pain points addressed, and past outreach history.
2. **Search for pain-point conversations**: Use `web_search_exa` to find developers discussing problems the product solves. Search terms should describe the pain, not the product:
   - Search relevant subreddits, Stack Overflow, GitHub Issues, and dev forums
   - Look for recent posts (past 7 days) asking about alternatives or expressing frustration
3. **Find complementary projects**: Use `web_search_exa` to discover projects in the same ecosystem that could benefit from integration or collaboration. Look for:
   - Projects that solve adjacent problems
   - Libraries or tools that share the same user base
   - Active maintainers who might be interested in partnerships
4. **Score opportunities**: For each finding, evaluate on a 1-10 relevance scale:
   - **9-10**: Direct pain match, active conversation, high visibility
   - **6-8**: Related problem, moderate visibility, worth engaging
   - **1-5**: Tangential, low priority — skip for now
5. **Draft 2-3 outreach messages**: For the top-scored opportunities, write messages that:
   - Lead with help, not promotion (answer their question first)
   - Reference their specific situation
   - Mention the product only if it genuinely solves their problem
   - Include a link to relevant docs or examples
   - Keep under 200 words
6. **Create follow-up tasks**: Add TODO tasks in `org/next_actions.org` with `:comms:outreach:` tags for any conversations that need follow-up in 3-7 days.
7. **Log to journal**: Call `datacore.capture` with opportunities found, scores, drafted messages, and follow-up plan.

## Output

- 2-3 draft outreach messages (ready for posting or sending)
- Follow-up tasks in `org/next_actions.org` with `:comms:outreach:` tags
- Journal entry with opportunity analysis and scores

## Success Criteria

- Outreach messages lead with value, not self-promotion
- Each message references the specific context it responds to
- Only opportunities scoring 6+ are acted on
- Follow-up tasks have specific dates (not open-ended)
