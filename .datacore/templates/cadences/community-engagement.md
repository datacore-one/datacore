---
cadence: community-engagement
role: cmo
frequency: weekly
duration: 30min
tools: [web_search_exa, plur_recall_hybrid, datacore.capture]
---

## Objective

Monitor community activity across platforms, recognize contributors, and maintain an active presence in community discussions.

## Steps

1. **Load venture context**: Call `plur_recall_hybrid` with the venture name to retrieve GitHub org, community channels, key community members, and engagement history.
2. **Check GitHub activity**: Use `web_search_exa` to search `site:github.com "{github_org}"` for recent Discussions, Issues, and PRs from external contributors (past 7 days).
3. **Check Reddit and HN**: Use `web_search_exa` to search for `"{venture_name}"` OR `"{product_name}"` on `site:reddit.com` and `site:news.ycombinator.com` — past 7 days.
4. **Check community forums**: Search for mentions in relevant developer communities (Discord servers, Discourse forums, dev.to, Stack Overflow) using `web_search_exa`.
5. **Identify top contributors**: List people who opened PRs, filed detailed bug reports, answered community questions, or wrote about the project. Note repeat contributors.
6. **Draft thank-you messages**: For top contributors, draft personalized acknowledgments. Reference their specific contribution. Keep it genuine — no template-sounding messages.
7. **Answer unanswered questions**: Identify community questions that went unanswered. Draft helpful responses with links to docs or examples where applicable.
8. **Log community health summary**: Call `datacore.capture` with:
   - Total community touchpoints this week
   - New contributors identified
   - Unanswered questions found and addressed
   - Sentiment trend (improving / stable / declining)
   - Top contributors and their contributions

## Output

- Draft thank-you messages for top contributors (ready for posting)
- Draft answers to unanswered community questions
- Journal entry with community health summary

## Success Criteria

- All community platforms are checked (not just GitHub)
- Top contributors are identified and acknowledged
- No community question older than 7 days is left unanswered
- Community health trend is tracked week-over-week
