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
4. **State-check filter (run BEFORE scoring)**: For each candidate, verify the thread is still actionable. Reject any candidate that fails — surface a "rejected, with reason" line per candidate so the funnel is auditable.
   - **GitHub issues**: must be `state == OPEN`, `closed == false`, `locked == false`. Verify with `gh issue view <n> --repo <owner/repo>` or `gh api repos/<owner>/<repo>/issues/<n>`.
   - **GitHub discussions**: must be `state == OPEN`. Reject if the last maintainer/OWNER comment in the thread is a substantive answer to the OP's question (heuristic: OWNER/MEMBER comment >200 chars within the last 14 days → assume answered). Verify with `gh api repos/<owner>/<repo>/discussions/<n>/comments`.
   - **Articles / blog posts**: `published_at` must be within the freshness window declared in the post type's posting note (DEV.to: 30 days; long-form blogs: 90 days). Verify by reading the article's published-date metadata.
   - Without this filter, prior-month runs scored 3 dead targets at 7+/10 and produced un-postable drafts (see plur 2026-04-30 cycle: 3-of-3 stale; net sends = zero).
5. **Score opportunities**: For each finding that passed the state check, evaluate on a 1-10 relevance scale:
   - **9-10**: Direct pain match, active conversation, high visibility
   - **6-8**: Related problem, moderate visibility, worth engaging
   - **1-5**: Tangential, low priority — skip for now
6. **Draft 2-3 outreach messages**: For the top-scored opportunities, write messages that:
   - Lead with help, not promotion (answer their question first)
   - Reference their specific situation
   - Mention the product only if it genuinely solves their problem
   - Include a link to relevant docs or examples
   - Keep under 200 words
7. **Create follow-up tasks**: Add TODO tasks in `org/next_actions.org` with `:comms:outreach:` tags for any conversations that need follow-up in 3-7 days.
8. **Log to journal**: Call `datacore.capture` with opportunities found, scores, drafted messages, and follow-up plan.

## Output

- 2-3 draft outreach messages (ready for posting or sending)
- Follow-up tasks in `org/next_actions.org` with `:comms:outreach:` tags
- Journal entry with opportunity analysis and scores

## Success Criteria

- Outreach messages lead with value, not self-promotion
- Each message references the specific context it responds to
- Every candidate passes the state-check filter (step 4) before scoring; rejected candidates are listed with reason
- Only opportunities scoring 6+ are acted on
- Follow-up tasks have specific dates (not open-ended)
