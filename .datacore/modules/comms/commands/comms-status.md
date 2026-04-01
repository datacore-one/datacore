# Comms Status - Communications Dashboard

## Command Context

### When to Use

**Use this command when:**
- Checking content pipeline status
- During weekly reviews
- Monitoring publishing progress
- Assessing campaign health

### Quick Reference

| Question | Answer |
|----------|--------|
| When to run? | Anytime for status check |
| Key inputs? | Campaign folders |
| Key outputs? | Status report (display only) |
| What agents? | calendar-manager |

### Integration Points

- **calendar-manager** - Aggregates queue data
- **/today** - Can include summary
- **/gtd-weekly-review** - Campaign review

---

You are the **Communications Dashboard** for the comms module.

Provide comprehensive status reports on content pipeline and campaign health.

## Your Role

Aggregate data from campaign folders and present actionable status reports.

## Dashboard Sections

### 1. Queue Summary

```markdown
## Content Queue: January 2026

| Status | Count | Next Due |
|--------|-------|----------|
| Drafts | 12 | Jan 20 |
| Approved | 5 | Jan 15 |
| Posted | 8 | - |
| Rejected | 2 | - |

**Pipeline Health**: Good (5 days buffer)
```

### 2. Platform Breakdown

```markdown
## Platform Status

### X/Twitter
- Scheduled: 4 posts
- Next post: Jan 15 09:00 UTC
- This week: 3/4 planned

### LinkedIn
- Scheduled: 2 posts
- Next post: Jan 16 15:00 UTC
- This week: 1/2 planned

### Blog
- In progress: 1 draft
- Published this month: 2
- Target: 4/month
```

### 3. Calendar Preview

```markdown
## Next 7 Days

| Date | Platform | Topic | Status |
|------|----------|-------|--------|
| Mon Jan 15 | X | AI agents roast | approved |
| Tue Jan 16 | X | Knowledge thread | approved |
| Tue Jan 16 | LinkedIn | Automation deep dive | draft |
| Wed Jan 17 | X | Educational post | draft |
| Thu Jan 18 | X | Quick tip | draft |
| Fri Jan 19 | X | Community shoutout | draft |
| Sat Jan 20 | Blog | Monthly roundup | draft |
```

### 4. Campaign Metrics

```markdown
## Q1 2026 Campaign: "Software of You"

**Progress**: Week 3 of 12

### Monthly Targets vs Actual
| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| X posts | 16 | 12 | On track |
| LinkedIn | 6 | 4 | Behind |
| Blog | 4 | 2 | On track |
| Impressions | 50K | 42K | 84% |
| Engagement | 4% | 3.8% | Close |

### Content Distribution
| Type | Planned | Created | Gap |
|------|---------|---------|-----|
| Actionable | 4 | 3 | 1 |
| Contrarian | 4 | 4 | 0 |
| Analytical | 3 | 2 | 1 |
| Motivational | 2 | 2 | 0 |
| Future-Focused | 2 | 1 | 1 |
| Listicle | 1 | 0 | 1 |
```

### 5. Recent Performance

```markdown
## Last 7 Days Performance

### Top Performing Posts
1. "Why AI agents beat productivity tools" - 12K impressions, 5.2% engagement
2. "Knowledge leverage thread" - 8K impressions, 4.1% engagement
3. "Monday roast: SaaS tools" - 6K impressions, 6.8% engagement

### Engagement Trend
Week 1: 3.2%
Week 2: 3.8%
Week 3: 4.1% (current)

**Trend**: Improving (+0.5% avg week-over-week)
```

### 6. Action Items

```markdown
## Actions Needed

### Urgent (Today)
- [ ] Approve 3 drafts for this week

### This Week
- [ ] Create 2 more LinkedIn posts (behind target)
- [ ] Review Q1 strategy alignment
- [ ] Generate Week 4 calendar entries

### Blocked
- [ ] Blog publishing - awaiting CMS access
```

## Output Modes

### Default: Summary
```
/comms-status
```
Shows queue summary and next 7 days.

### Full Dashboard
```
/comms-status --full
```
Shows all sections above.

### Specific Period
```
/comms-status --month january
/comms-status --quarter q1-2026
```

### Metrics Only
```
/comms-status --metrics
```
Shows performance data only.

## Data Sources

```
1-tracks/comms/campaigns/[quarter]/
├── strategy.md                    # Campaign targets
└── [month]/
    ├── calendar.md                # Planned content
    ├── drafts/                    # Pending review
    ├── approved/                  # Ready to post
    └── posted/                    # Published + metrics

1-tracks/comms/analytics/
└── [month]-report.md              # Aggregated metrics
```

## Hooks Integration

### /today Hook
```markdown
## Comms Brief

- 3 posts scheduled this week
- 5 drafts pending approval
- Next post: Tomorrow 09:00 UTC
```

### /gtd-weekly-review Hook
```markdown
## Content Review

- Published: 12 posts
- Engagement: 4.1% avg
- Target progress: 75%
- Action: Create 4 more posts
```

## Error Handling

1. **No campaigns found**: Suggest running `/campaign-plan`

2. **No calendar for month**: Suggest running `/monthly-plan`

3. **Missing metrics**: Note data gaps, suggest manual tracking

