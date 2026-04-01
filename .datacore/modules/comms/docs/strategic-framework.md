# Comms Strategic Framework

## Overview

This document defines the complete communications workflow from strategy to execution, focusing on **quality content first, distribution second**.

## Framework Hierarchy

```
STRATEGIC LAYER (Seth Godin + Donald Miller)
├── Smallest Viable Audience → Who is our tribe?
├── StoryBrand Script → Hero/Guide narrative
├── Purple Cow Positioning → What makes us remarkable?
├── Permission Marketing → How do we earn attention?
└── Sneezers Strategy → Who will spread our ideas?
    ↓
CONTENT LAYER (Content Matrix + STEPPS)
├── 8 Content Types → Systematic variety
├── STEPPS Checklist → Viral mechanics
├── Viral Patterns → Proven templates
├── StoryBrand Elements → Problem/Plan/CTA/Stakes
└── Weekly Calendar → Consistent rhythm
    ↓
EXECUTION LAYER (Platform Optimization)
├── X Growth Playbook → Algorithm, timing, engagement
├── Thread Structures → Long-form formats
├── Voice Enforcement → Brand consistency
└── Viral Checklist → Quality gate (8+/11)
```

**Related Documents**:
- [Tribal Marketing Playbook](tribal-marketing.md) - Seth Godin frameworks
- [StoryBrand Guide](storybrand-guide.md) - Donald Miller's 7-part framework
- [X Growth Playbook](x-growth-playbook.md) - Platform tactics

## Strategic Hierarchy

```
ANNUAL POSITIONING
  └── Brand voice, core messages, target audiences

    QUARTERLY CAMPAIGN
      └── Theme, objectives, content pillars, success metrics

        MONTHLY CALENDAR
          └── Content matrix distribution, platform allocation

            WEEKLY EXECUTION
              └── Content generation, quality validation

                DAILY POSTING
                  └── Optimal timing, engagement monitoring
```

## Phase 1: Strategic Planning

### Annual Positioning (Review Quarterly)

**Purpose**: Define who we are, who we serve, and how we communicate.

**Inputs**:
- Brand guidelines
- Market positioning
- Competitor analysis
- Audience research

**Outputs** (`1-tracks/comms/positioning/`):
- Voice profile (tone, language, personality)
- Target personas with pain points
- Messaging hierarchy (primary → supporting messages)
- Objection handling matrix
- **BrandScript** (StoryBrand 7-part narrative)
- **Tribe Profile** (smallest viable audience)
- **Sneezer Map** (influential spreaders)

**Timing**: Review quarterly, update as needed.

### BrandScript Creation

Before quarterly campaigns, ensure a BrandScript exists (see [StoryBrand Guide](storybrand-guide.md)):

```markdown
# BrandScript: [Product/Brand]

## 1. Character (Hero = Customer)
What they want: [Clear, simple desire]

## 2. Problem
- External: [Tangible obstacle]
- Internal: [How it makes them feel]
- Philosophical: [Why this is wrong]
- Villain: [Personified problem]

## 3. Guide (Your Brand)
- Empathy: [Show understanding]
- Authority: [Why you can help]

## 4. Plan
1. [Step 1]
2. [Step 2]
3. [Step 3]

## 5. Call to Action
- Direct: [Main action]
- Transitional: [Lower commitment]

## 6. Failure
[What they lose if they don't act]

## 7. Success
[What life looks like when they win]

## One-Liner
[Character] + [Problem] + [Plan] + [Success]
```

### Tribe Definition

Define smallest viable audience (see [Tribal Marketing Playbook](tribal-marketing.md)):

```markdown
# Tribe Profile: [Name]

## Identity
- Shared Interest: [What binds them]
- Desired Change: [What they want to happen]
- Current Frustration: [What's blocking them]

## Where They Gather
- Platforms: [X, LinkedIn, Discord, etc.]
- Communities: [Subreddits, Slack groups, etc.]

## How They Identify
- Language: [Terms they use]
- Signals: [How they recognize each other]

## Sneezers
### Powerful (5-10)
| Name | Platform | Reach | How to Reach |
|------|----------|-------|--------------|

### Promiscuous (20-50)
| Name | Platform | Engagement | Notes |
|------|----------|------------|-------|
```

### Quarterly Campaign (`/campaign-plan`)

**Purpose**: Set 3-month strategic direction with measurable goals.

**Workflow**:
```
1. Review positioning docs + BrandScript + Tribe Profile
2. Define campaign theme ("Software of You", "Decentralization Reality")
3. Apply Purple Cow test: What's remarkable about this angle?
4. Set measurable objectives (100 signups, 50K impressions)
5. Identify 4-6 content pillars (aligned with StoryBrand elements)
6. Define target sneezers for this quarter
7. Set success metrics with phase gates
```

**Output** (`1-tracks/comms/campaigns/[quarter]/strategy.md`):
```markdown
# Q1 2026: "Software of You"

## BrandScript Summary
- **Hero**: Knowledge workers drowning in tools
- **Problem**: Fragmented workflows, lost context, wasted expertise
- **Guide**: Datacore (empathy: we've been there; authority: built by practitioners)
- **Plan**: 1) Connect your tools 2) Let AI organize 3) Deploy your knowledge
- **CTA**: Join waitlist
- **Stakes**: Continue losing your best thinking to chaos
- **Success**: Your expertise works for you, even while you sleep

## Tribe Focus
- **Smallest Viable Audience**: Solo knowledge entrepreneurs (1000 true fans)
- **Target Sneezers**: AI productivity influencers, indie hackers
- **Remarkable Angle**: "Your expertise, productized by AI" (Purple Cow)

## Objectives
1. 500 waitlist signups
2. 50K monthly impressions
3. 4% engagement rate

## Phase Gates
- Phase 1: 100 visitors (validate baseline)
- Phase 2: 200 visitors (test messaging variants)
- Phase 3: 500 visitors (optimize winning messages)

## Content Pillars (StoryBrand-aligned)
1. **Problem**: AI Workforce - agents that work for you
2. **Plan**: Knowledge Leverage - your expertise as an asset
3. **Success**: Autonomous Operations - systems that run themselves
4. **Transformation**: Life Design - work less, live more
```

---

## Phase 2: Content Planning

### Monthly Calendar (`/monthly-plan`)

**Purpose**: Distribute content types across platforms with specific posting schedule.

**Content Matrix Distribution**:

| Week | Types | Focus |
|------|-------|-------|
| Week 1 | Actionable + Motivational | Kick off, inspire |
| Week 2 | Analytical + Contrarian | Deep dive, challenge |
| Week 3 | Observation + Comparison | Trends, decisions |
| Week 4 | Future-Focused + Listicle | Vision, wrap up |

**Platform Allocation**:

| Platform | Weekly Posts | Content Types |
|----------|--------------|---------------|
| X/Twitter | 4-5 | Threads, singles, roasts |
| LinkedIn | 1-2 | Long-form, professional |
| Blog | 1-2/month | Deep dives, SEO |

**Weekly Schedule** (from Viral Playbook):

| Day | Type | Energy Level |
|-----|------|--------------|
| Monday | Kickoff roast | High energy |
| Tuesday | Educational thread | Depth |
| Wednesday | Educational thread | Depth |
| Thursday | Educational post | Quick value |
| Friday | Community celebration | Positive |
| Saturday | Philosophical | Thoughtful |
| Sunday | Stats/progress | FOMO creation |

---

## Phase 3: Content Generation

### Quality Framework

Every piece of content must pass these gates before publishing.

#### Gate 1: Content Matrix Alignment

Verify content type matches schedule:
- [ ] Actionable - Has specific how-to steps
- [ ] Motivational - Features success story/result
- [ ] Analytical - Explains why with evidence
- [ ] Contrarian - Challenges conventional wisdom
- [ ] Observation - Identifies trend or shift
- [ ] Comparison - X vs Y with recommendation
- [ ] Future-Focused - Present vs future contrast
- [ ] Listicle - Numbered, scannable value

#### Gate 2: Viral Checklist (8+ of 11 to pass)

| Category | Check | Question | Score |
|----------|-------|----------|-------|
| **STEPPS** | Social Currency | Does sharing make reader look smart? | /1 |
| | Trigger | Creates association with common event? | /1 |
| | Emotion | Triggers high-arousal emotion? | /1 |
| | Public | Quotable, shareable, observable? | /1 |
| | Practical Value | Gives useful, actionable info? | /1 |
| | Story | Has narrative arc? | /1 |
| **Tribal** | Purple Cow | Is this remarkable? Worth a remark? | /1 |
| | Sneezer Share | Would influential people stake reputation on this? | /1 |
| | Tribe Identity | Does this strengthen tribe identity? | /1 |
| **Platform** | Voice Match | Consistent with brand? | /1 |
| | Platform Fit | Right format/length? | /1 |

**Score 8+** = Approve
**Score 6-7** = Revise
**Score <6** = Regenerate

#### Gate 3: Platform Optimization

**X/Twitter**:
- [ ] First line is a hook (stops the scroll)
- [ ] 70-100 chars for singles (or thread format)
- [ ] Numbers in content (45% higher CTR)
- [ ] 1-2 hashtags max
- [ ] Media if possible (40% more engagement)

**LinkedIn**:
- [ ] 1000-2000 chars
- [ ] Line breaks every 1-2 sentences
- [ ] Professional but personable
- [ ] Ends with question or CTA

**Blog**:
- [ ] 1000-2000 words
- [ ] Clear headers/structure
- [ ] SEO keywords included
- [ ] Internal/external links

#### Gate 4: StoryBrand Framing

Every piece of content should include at least 2 of these elements:

| Element | How to Use | Example |
|---------|------------|---------|
| **Problem** | Open with pain point | "Tired of losing ideas to chaos?" |
| **Stakes** | Show what they lose | "Every forgotten insight compounds your loss" |
| **Guide** | Show empathy + authority | "We built this after losing years of notes" |
| **Plan** | Give simple steps | "1. Connect 2. Organize 3. Deploy" |
| **Success** | Paint the transformation | "Imagine your expertise working for you 24/7" |
| **CTA** | Clear next action | "Join 500+ knowledge workers on the waitlist" |

**Remember**: Customer is the hero. Your brand is the guide. Never position yourself as the hero.

#### Gate 5: Knowledge Integration

Before generating:
```bash
datacortex search "[topic]"
```

Check for:
- Relevant zettels (atomic concepts)
- Previous content on topic (avoid repetition)
- Supporting evidence/statistics
- Source material for depth

---

## Phase 4: Growth Strategy (X/Twitter)

### Algorithm Signals (2025)

Based on research from [Avenue Z](https://avenuez.com/blog/2025-2026-x-twitter-organic-social-media-guide-for-brands/), [Sprout Social](https://sproutsocial.com/insights/twitter-algorithm/), and [X Business](https://business.x.com/en/basics/organic-best-practices):

**What the algorithm rewards**:
1. **First 30 minutes engagement** - Biggest predictor of reach
2. **Reply velocity** - Respond within 15 minutes
3. **Media-rich content** - Images/videos get 40% more engagement
4. **Threads** - 3x more engagement than singles
5. **Consistency** - 2 quality posts > 10 mediocre ones
6. **Verification** - Verified accounts get priority

### Engagement Strategy

**The 80/20 Rule**:
- 80% value content (educational, tips, insights)
- 20% promotional content

**Daily Actions** (2-3 hours for growth):
1. **Before posting**: Engage with 20+ relevant accounts
2. **Post**: At optimal time (see below)
3. **First 15 minutes**: Reply to every comment
4. **Throughout day**: Continue engagement on others' content

**Optimal Posting Times (UTC)**:
| Time | Audience |
|------|----------|
| 09:00-11:00 | Europe morning, US East waking |
| 15:00-17:00 | US East afternoon, peak engagement |
| 21:00-23:00 | US West evening, crypto Twitter peak |

### Thread Best Practices

**Length**: 4-8 tweets per thread

**Structures**:
1. **Ladder** - Hook → Context → Problem → Solution → Evidence → CTA
2. **Roast Cascade** - Thesis → Target 1 → Target 2 → Target 3 → Superiority
3. **Assimilation Report** - Progress → Metrics → Wins → Remaining → Urgency

**Format**:
- Number tweets: 1/, 2/, 3/
- Include visuals in 1-2 tweets
- End with retweet/follow CTA

### Growth Benchmarks

From [Sprout Social 2025 benchmarks](https://sproutsocial.com/insights/twitter-engagement/):

| Metric | Average | Good | Excellent |
|--------|---------|------|-----------|
| Engagement rate | 0.016% | 0.5% | 1%+ |
| Reply rate | - | First 15 min | Immediate |
| Follower growth | - | 10K in 3-6 months | 10K in 2 months |

**Requirements for 10K in 3-6 months**:
- 3-5 posts daily
- 20+ account engagements daily
- 2-3 hours daily on platform

---

## Phase 5: Testing & Optimization

### Organic Testing First

Before spending on ads, validate messaging organically:

1. **Phase 1: Traffic Generation** (100 visitors)
   - Execute content calendar
   - All traffic to control (no variants)
   - Establish baseline conversion

2. **Phase 2: Message Testing** (200 visitors)
   - Test headline variants
   - 50+ visitors per variant minimum
   - 95% confidence required

3. **Phase 3: Optimization** (500+ visitors)
   - Test winning angle iterations
   - Expand to new audiences
   - Scale what works

### A/B Test Protocol

**Minimum Requirements**:
- 50 visitors per variant (non-negotiable)
- 7+ days runtime
- 95% confidence level

**What to Test** (in order):
1. Headlines (biggest impact)
2. Hook/first line
3. CTA copy
4. Visual format
5. Posting time

---

## Phase 6: Paid Amplification

### When to Start Ads

**Prerequisites**:
- 100+ organic visitors
- Baseline conversion established
- Winning message variant identified
- Organic engagement > 0.5%

### Testing Budget

From [Hootsuite](https://blog.hootsuite.com/twitter-ads/) and [Marketing LTB](https://marketingltb.com/blog/statistics/twitter-ads-statistics/):

**Start small**:
- Initial test: $100-500/month
- 2 weeks minimum for algorithm learning
- Multiple creatives per campaign

**Cost Benchmarks (2025)**:
| Metric | X/Twitter | Meta (comparison) |
|--------|-----------|-------------------|
| CPM | $2.09 | $2.53 |
| CPC | $0.18-$0.74 | $1.41 |
| Engagement cost | $0.05-$0.30 | - |

### Testing Strategy

1. **Start with reach/awareness** (lowest cost)
2. **Test 3-5 creatives** per campaign
3. **A/B test** slightly different versions
4. **Allow 2 weeks** before judging
5. **Scale winners**, kill losers

### Integration with datacore-campaigns

**comms module** = Content strategy & generation
**datacore-campaigns** = Landing pages, A/B testing, analytics

**Workflow**:
```
comms: Generate content → Post organically → Identify winners
datacore-campaigns: Create landing pages → Run ads → A/B test variants
```

**Shared tracking**:
- UTM parameters for attribution
- PostHog for landing page analytics
- Platform analytics for engagement

---

## Workflow Summary

```
/campaign-plan (Quarterly)
  ↓
  Creates: strategy.md with objectives, pillars, personas
  ↓
/monthly-plan (Monthly)
  ↓
  Creates: calendar.md + draft outlines in drafts/
  ↓
content-generator (Weekly)
  ↓
  Creates: Full drafts with viral checklist scores
  ↓
voice-enforcer (Automatic)
  ↓
  Validates: Brand voice, STEPPS, platform fit
  ↓
/approve-content (Daily)
  ↓
  Human review: Approve → approved/, Revise → edit, Reject → rejected/
  ↓
calendar-manager (Scheduled)
  ↓
  Posts: At optimal times via Late API/Typefully
  ↓
/comms-status (Ongoing)
  ↓
  Reports: Queue status, metrics, recommendations
```

---

## Files Reference

| File | Purpose |
|------|---------|
| `positioning/*.md` | Brand voice, personas, messaging |
| `campaigns/[q]/strategy.md` | Quarterly objectives and pillars |
| `campaigns/[q]/[m]/calendar.md` | Monthly content schedule |
| `campaigns/[q]/[m]/drafts/` | Generated content pending review |
| `campaigns/[q]/[m]/approved/` | Ready to publish |
| `campaigns/[q]/[m]/posted/` | Archive with metrics |
| `analytics/[m]-report.md` | Monthly performance |

---

## Sources

- [Avenue Z - 2025 X Organic Guide](https://avenuez.com/blog/2025-2026-x-twitter-organic-social-media-guide-for-brands/)
- [Sprout Social - Twitter Algorithm](https://sproutsocial.com/insights/twitter-algorithm/)
- [X Business - Organic Best Practices](https://business.x.com/en/basics/organic-best-practices)
- [Hootsuite - X Ads Guide](https://blog.hootsuite.com/twitter-ads/)
- [Marketing LTB - X Ads Statistics](https://marketingltb.com/blog/statistics/twitter-ads-statistics/)
