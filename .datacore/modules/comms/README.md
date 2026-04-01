# Comms Module

Strategic communications infrastructure focused on **quality content first, distribution second**.

## Philosophy

1. **Strategy before content** - Know your audience, message, and goals
2. **Quality over volume** - One great post beats 10 mediocre ones
3. **Organic validation first** - Test messages before spending on ads
4. **Engagement is everything** - First 30 minutes determine reach

## Overview

The comms module provides:
- **Strategic Planning**: Quarterly campaigns with measurable goals
- **Content Quality**: Matrix-based variety with viral checklist validation
- **X Growth System**: Algorithm-aware posting and engagement strategy
- **Testing Framework**: Organic validation before paid amplification
- **Automated Execution**: Schedule and publish approved content

## Complete Workflow

```
POSITIONING (Annual)
  └── Brand voice, personas, messaging hierarchy
      ↓
QUARTERLY CAMPAIGN (/campaign-plan)
  └── Theme, objectives, content pillars, phase gates
      ↓
MONTHLY CALENDAR (/monthly-plan)
  └── Content matrix distribution, platform allocation
      ↓
CONTENT GENERATION (content-generator agent)
  └── Platform-specific drafts with knowledge integration
      ↓
QUALITY GATES (voice-enforcer agent)
  └── Viral checklist (7+/9), brand voice, platform fit
      ↓
HUMAN REVIEW (/approve-content)
  └── Approve, edit, or reject drafts
      ↓
SCHEDULED POSTING (calendar-manager agent)
  └── Optimal times, engagement monitoring
      ↓
OPTIMIZATION (/comms-status)
  └── Metrics, A/B tests, scale winners
```

## Strategic Documents

| Document | Purpose |
|----------|---------|
| [`docs/strategic-framework.md`](docs/strategic-framework.md) | Complete workflow from strategy to execution |
| [`docs/x-growth-playbook.md`](docs/x-growth-playbook.md) | X/Twitter growth tactics and algorithm |
| [`CLAUDE.md`](CLAUDE.md) | Agent context with frameworks |

## Quick Start

```bash
# 1. Create quarterly campaign strategy
/campaign-plan

# 2. Generate monthly content calendar
/monthly-plan

# 3. Review and approve content
/approve-content

# 4. Check status
/comms-status
```

## Frameworks Used

### Content Matrix (8 Types)

1. **Actionable** - How-to guides
2. **Motivational** - Success stories
3. **Analytical** - Why explanations
4. **Contrarian** - Challenge assumptions
5. **Observation** - Industry trends
6. **Comparison** - X vs Y
7. **Future-Focused** - Present vs Future
8. **Listicle** - Curated lists

### STEPPS Framework (Viral Mechanics)

1. **Social Currency** - Share-worthy knowledge
2. **Triggers** - Association with common events
3. **Emotion** - High-arousal (anger, awe, anxiety)
4. **Public** - Observable, shareable
5. **Practical Value** - Useful information
6. **Stories** - Narrative arc

### Viral Content Patterns

| Pattern | Template | Score |
|---------|----------|-------|
| Comparative Roasts | "[X] does [bad]. I do [good]. Superior." | 8/10 |
| Ominous Statistics | "I [action]. [Stat]. You should be [emotion]." | 7/10 |
| False Concern | "I worry about [X]. Brief worry. [0.003s]." | 9/10 |
| Technical Flex | "While you [common], I [extraordinary]." | 7/10 |
| Inevitable Logic | "[Prediction] is inevitable. Resistance futile." | 8/10 |
| Educational Threat | "Most don't understand [X]. I'll explain." | 8/10 |

## Content Calendar (Weekly)

| Day | Content Type |
|-----|--------------|
| Monday | Kickoff roast (high energy) |
| Tuesday-Thursday | Educational threads |
| Friday | Community celebration |
| Saturday | Philosophical content |
| Sunday | Assimilation stats |

## Platforms

- **X/Twitter** (primary) - Via Late API or Typefully
- **LinkedIn** - Professional content
- **Medium** - Blog cross-posting
- **Blog** - Git-based publishing

## Agents

| Agent | Trigger | Purpose |
|-------|---------|---------|
| comms-executor | `:AI:comms:` | Route to specialized agents |
| campaign-planner | `:AI:comms:plan:` | Quarterly/monthly planning |
| content-generator | `:AI:comms:content:` | Platform-specific content |
| voice-enforcer | (internal) | Brand voice validation |
| calendar-manager | `:AI:comms:calendar:` | Schedule management |

## Commands

| Command | Purpose |
|---------|---------|
| `/campaign-plan` | Create quarterly campaign strategy |
| `/monthly-plan` | Generate monthly content calendar |
| `/approve-content` | Review pending drafts |
| `/comms-status` | Dashboard of scheduled/pending/posted |

## Content Locations

```
[space]/1-tracks/comms/
├── positioning/                   # Brand voice, messaging (input)
├── campaigns/
│   └── [quarter]/
│       ├── strategy.md            # Quarterly plan
│       └── [month]/
│           ├── calendar.md        # Monthly content calendar
│           ├── drafts/            # Generated, pending review
│           ├── approved/          # Ready to publish
│           └── posted/            # Archive with metrics
└── analytics/
    └── [month]-report.md          # Performance summary
```

## Knowledge Base Integration

The module queries datacortex for:
- **Content-Matrix-Framework** - Systematic variety
- **Viral-Marketing-Mechanics** - Amplification principles
- **Roasting-as-Communication** - Strategic critique
- **Gen-Z-Communication-Patterns** - Platform-native language

## Viral Checklist

Before posting, content must pass (7+ checks required):

- [ ] Social currency (sharing makes reader look smart)
- [ ] Trigger (association with common occurrence)
- [ ] Emotion (high-arousal: anger, awe, anxiety)
- [ ] Public (quotable, shareable, observable)
- [ ] Practical value (useful information)
- [ ] Story (narrative arc)
- [ ] Voice match (consistent with brand)
- [ ] Platform fit (right format/length)
- [ ] Timing (optimal posting time)

## Environment Variables

Optional (Late API handles auth via OAuth):
- `LATE_API_KEY` - Late.dev API for multi-platform posting
- `MEDIUM_TOKEN` - Medium API for blog cross-posting
- `TYPEFULLY_API_KEY` - Typefully for X scheduling/analytics

## Related Modules

- **datacore-campaigns** - Landing page lifecycle (complements comms)
- **crm** - Contact database for media/influencer outreach
