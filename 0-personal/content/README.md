# Content - AI-Generated Output Repository

**Purpose:** Central repository for all AI-generated content created by autonomous GTD agents

**Last Updated:** 2025-11-23

---

## Structure

```
content/
├── blog/           # Blog posts and articles (gtd-content-writer)
├── social/         # Social media content (gtd-content-writer)
├── emails/         # Email drafts (gtd-content-writer)
├── docs/           # Technical documentation (gtd-content-writer)
├── marketing/      # Marketing copy and materials (gtd-content-writer)
├── reports/        # Data analysis reports (gtd-data-analyzer)
├── summaries/      # Research summaries (research-link-processor)
└── podcasts/       # Podcast transcripts and notes
```

---

## Agent Output Locations

### gtd-content-writer
**Description:** Creates blog posts, emails, social media content, documentation, and marketing materials

**Output Locations:**
- `blog/` - Blog posts (1000-2000 words)
- `social/` - Twitter threads, LinkedIn posts (280-1500 chars)
- `emails/` - Email drafts (investor updates, outreach, partnerships)
- `docs/` - Technical documentation (API guides, user guides)
- `marketing/` - Marketing copy (landing pages, product descriptions)

**Filename Format:** `YYYY-MM-DD-[topic]-[type].md`
- Example: `2025-11-25-datafund-privacy-blog.md`
- Example: `2025-11-25-investor-update-email.md`
- Example: `2025-11-25-launch-tweets.md`

**Status:** All output marked as **DRAFT** - requires human review before publication

### gtd-data-analyzer
**Description:** Extracts data from journals and org files, calculates metrics, generates insights

**Output Locations:**
- `reports/` - Data analysis reports (weekly GTD metrics, monthly trading performance, project dashboards)

**Filename Format:** `YYYY-MM-DD-[topic]-[type]-report.md`
- Example: `2025-11-25-gtd-weekly-metrics-report.md`
- Example: `2025-11-30-trading-november-performance-report.md`

**Report Types:**
- GTD productivity metrics
- Trading performance analysis
- Project status reports
- Work area analytics
- Habit tracking summaries

### research-link-processor
**Description:** Batch URL processing for research tasks

**Output Locations:**
- `summaries/` - Research summaries (executive summaries of fetched URLs)
- `reports/` - Detailed research reports (comprehensive analysis with links to zettels)

**Filename Format:** `YYYY-MM-DD-[topic]-summary.md` / `YYYY-MM-DD-[topic]-report.md`
- Example: `2025-11-02-datafund-research-links-summary.md`
- Example: `2025-11-02-datafund-research-links-report.md`

### gtd-research-processor
**Description:** Individual URL research with zettel generation

**Output Locations:**
- `/Users/gregor/Data/notes/pages/` - Literature notes and atomic zettels (NOT in content/)

**Note:** Research processor outputs to notes/pages/ for integration with Obsidian knowledge base

---

## File Format Standards

### Frontmatter (All Files)

All generated content includes YAML frontmatter:

**Blog Posts / Social / Emails / Docs / Marketing:**
```yaml
---
type: [blog/email/social/documentation/marketing]
category: [Datafund/Verity/Trading/Personal]
audience: [target audience]
status: draft
created: YYYY-MM-DD
task-source: [org-mode task headline]
review-required: true
---
```

**Reports:**
```yaml
---
type: data-report
category: [GTD/Trading/Datafund/Verity/Personal]
period: [week/month/custom]
period-start: YYYY-MM-DD
period-end: YYYY-MM-DD
generated: YYYY-MM-DD HH:MM
status: draft
task-source: [org-mode task headline]
---
```

**Summaries:**
```yaml
---
type: research-summary
source: [URL or multiple URLs]
created: YYYY-MM-DD
tags: [category, topic, research]
related-to: [work area]
---
```

### Footer (All Files)

All generated content includes footer attribution:

```markdown
---
**[Agent Name]** - Created: [Timestamp]
**Status:** DRAFT - Requires human review before publication
```

---

## Usage Guidelines

### For Users (Reviewing AI Output)

**Daily Review:**
1. During `/gtd-daily-start`, check for new content in:
   - `reports/` (overnight data analysis)
   - `blog/` or `social/` (scheduled content generation)
2. Review frontmatter for context (task-source, category)
3. Edit draft as needed
4. Update frontmatter: `status: draft` → `status: ready` or `status: published`

**Content Types:**
- **Drafts** - AI-generated, needs review (status: draft)
- **Ready** - Reviewed and ready to publish (status: ready)
- **Published** - Live/sent (status: published)

**Review Priorities:**
- Technical accuracy (docs, technical claims)
- Strategic framing (emails, marketing)
- Brand voice consistency (all content)
- Factual correctness (data reports)

### For Agents (Generating Output)

**File Creation:**
1. Create file with proper filename format
2. Include complete YAML frontmatter
3. Generate content following type-specific structure
4. Add footer with agent name and timestamp
5. Return file path in JSON response to ai-task-executor

**Quality Standards:**
- Mark all output as `status: draft`
- Include `review-required: true` in frontmatter
- Never claim content is "ready to publish"
- Flag strategic decisions with `review_notes` in JSON response
- Fail transparently if context insufficient

---

## Integration with GTD System

### Task Flow

```
inbox.org
    ↓
/gtd-daily-end processes → next_actions.org (with :AI: tags)
    ↓
ai-task-executor scans every 15 min
    ↓
Routes to specialized agents:
    ├─ :AI:content: → gtd-content-writer → content/[type]/
    ├─ :AI:research: → gtd-research-processor → notes/pages/
    ├─ :AI:data: → gtd-data-analyzer → content/reports/
    └─ :AI:pm: → gtd-project-manager → notes/1-active/
    ↓
Logs work in notes/journals/[date].md
    ↓
/gtd-daily-start reviews AI work → User approves/adjusts
```

### Cross-References

**From content/ to other systems:**
- Content drafts reference notes in `~/Data/notes/pages/` (wiki-links)
- Reports extract data from `~/Data/org/next_actions.org` and `~/Data/notes/journals/`
- All content traces back to org-mode task via `task-source` frontmatter

**To content/ from other systems:**
- org-mode tasks link to generated content: `[[file:~/Data/content/blog/2025-11-25-post.md]]`
- Journal entries log agent outputs with file paths
- Project notes reference data reports for metrics tracking

---

## Folder Details

### blog/
**Purpose:** Long-form articles and blog posts
**Length:** 1000-2000 words
**Structure:** Hook, problem/context, core argument, implications, application, conclusion
**Tone:** Educational, authoritative but accessible
**Review Focus:** Technical accuracy, brand voice

### social/
**Purpose:** Social media content (Twitter, LinkedIn, etc.)
**Length:** Twitter threads (3-8 tweets), LinkedIn posts (1000-1500 chars)
**Structure:** Hook, supporting points, evidence, conclusion/CTA
**Tone:** Conversational, valuable, engaging
**Review Focus:** Message clarity, engagement potential

### emails/
**Purpose:** Email drafts (investor updates, partnerships, outreach)
**Length:** 300-600 words
**Structure:** Clear subject, key highlight upfront, body, clear ask
**Tone:** Professional, confident, transparent
**Review Focus:** Strategic framing, tone appropriateness

### docs/
**Purpose:** Technical documentation and guides
**Length:** 500-1500 words
**Structure:** Overview, prerequisites, usage, parameters, examples, troubleshooting
**Tone:** Clear, precise, methodical
**Review Focus:** Technical accuracy, completeness

### marketing/
**Purpose:** Marketing copy and materials
**Length:** 100-500 words
**Structure:** Headline, problem, solution, social proof, CTA
**Tone:** Persuasive, benefits-focused, concise
**Review Focus:** Value proposition clarity, differentiation

### reports/
**Purpose:** Data analysis and metrics reports
**Length:** 1000-3000 words
**Structure:** Executive summary, detailed analysis, insights, recommendations
**Tone:** Analytical, data-driven, actionable
**Review Focus:** Data accuracy, insight quality, recommendation feasibility

### summaries/
**Purpose:** Research summaries (executive overviews)
**Length:** 500-1000 words
**Structure:** Source info, key findings, implications, connections
**Tone:** Analytical, synthesizing, objective
**Review Focus:** Accuracy of source representation, relevance

### podcasts/
**Purpose:** Podcast transcripts and notes
**Length:** Varies
**Structure:** Episode metadata, transcript, key points, timestamps
**Tone:** Conversational
**Review Focus:** Accuracy, key insights extraction

---

## Maintenance

**Daily:** Review new drafts during `/gtd-daily-start`
**Weekly:** Archive or delete outdated drafts, update status metadata
**Monthly:** Review folder sizes, organize by year if needed (e.g., `reports/2025/`)
**Quarterly:** Audit published content, identify reusable material

---

## Success Metrics

**Agent Performance:**
- Draft quality: >85% require only minor edits
- Completion rate: >90% of :AI: tasks successfully executed
- Review time: <15 minutes average draft-to-ready time

**Content Output:**
- Volume: Track items generated per agent per week
- Acceptance rate: % of drafts that proceed to publication
- Time savings: Hours saved vs manual content creation

---

*Part of the Data second brain system*
*See ~/Data/CLAUDE.md for full repository structure*
