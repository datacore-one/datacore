---
summary: "Communications infrastructure — brand positioning, content generation, scheduling, engagement, landing pages, ads, and analytics."
triggers: ["quarterly campaign plan", "monthly content calendar", "approve content", "comms dashboard", "brand positioning", "create landing page", "engagement", "ads performance"]
context: on_match
---

# Comms Module

## Purpose

End-to-end communications system covering brand positioning, content planning, generation, scheduling, engagement, landing pages, ads, and analytics. Built on StoryBrand (Donald Miller) and Tribal Marketing (Seth Godin) frameworks. Customer is the HERO; your brand is the GUIDE.

## Quick Start

> Say "quarterly campaign plan" to create a campaign strategy, or "approve content" to review pending drafts.

## How It Works

### Framework Hierarchy

```
STRATEGIC  — Smallest Viable Audience, StoryBrand, Purple Cow, Permission Marketing
CONTENT    — 8 Content Types, STEPPS Viral Checklist, Viral Patterns, Weekly Calendar
EXECUTION  — Platform formatting, Voice Enforcement, Viral score gate (8+/11)
```

### Content Pipeline

1. **Position** — BrandScript, voice guidelines, tribe profile
2. **Plan** — Quarterly strategy, monthly calendar (8 content types rotated)
3. **Generate** — Platform-specific posts using viral patterns + datacortex zettels
4. **Enforce** — Voice check + STEPPS viral checklist (score 8+ to publish)
5. **Verify** — All URLs return HTTP 200 with real content
6. **Schedule** — Publishing queue via Tweepy/nightshift or manual approval
7. **Analyze** — PostHog metrics, conversion reports, A/B tests

### Engagement Pipeline

Discovery of X conversations, reply drafting, Telegram-based approval, posting. Also Chrome-based feed engagement (likes, follows, reply drafts).

### Module vs Content (Critical)

- **Module** (`.datacore/modules/comms/`) = system code, agents, docs — stays clean
- **Content** (space folders) = campaigns, drafts, posted — grows with use. All generated content goes to space folders.

## Agents & Commands

| Name | Type | When to use |
|------|------|-------------|
| `comms-executor` | agent | Router — routes `:AI:comms:` tasks to specialists |
| `brand-positioning-agent` | agent | BrandScript, voice guidelines, tribe profile |
| `campaign-planner` | agent | Quarterly strategy with goals, phases, audiences |
| `content-planner` | agent | Monthly calendars, weekly themes, post assignments |
| `content-generator` | agent | Platform-specific content using matrix + viral patterns |
| `voice-enforcer` | agent | Brand voice validation (called by content-generator) |
| `calendar-manager` | agent | Publishing queue and scheduling automation |
| `landing-generator` | agent | Landing page copy; deploy via `dev` module |
| `ads-optimizer` | agent | X Ads management, A/B tests, budget optimization |
| `metrics-analyzer` | agent | PostHog metrics, conversion reports |
| `web3-adapter` | agent | Cross-platform adaptation (X to Farcaster/Telegram/Discord) |
| `/campaign-plan` | command | Create quarterly campaign strategy |
| `/monthly-plan` | command | Generate monthly content calendar |
| `/engagement` | command | Run X engagement pipeline |
| `/engage` | command | Chrome-based X feed engagement |
| `/landing` | command | Create/update landing pages |
| `/ads` | command | Manage X Ads campaigns |

## Content Matrix (8 Types)

Actionable, Motivational, Analytical, Contrarian, Observation, Comparison, Future-Focused, Listicle.

## Weekly Calendar

Mon: kickoff roast | Tue-Thu: educational | Fri: community | Sat: philosophical | Sun: stats/FOMO.

## STEPPS Viral Checklist (11 checks, 8+ to publish)

Social Currency, Trigger, Emotion, Public, Practical Value, Story, Purple Cow, Sneezer Share, Tribe Identity, Voice Match, Platform Fit.

## Key Paths

| Path | Purpose |
|------|---------|
| `1-tracks/comms/positioning/` | Brand voice, messaging (input) |
| `1-tracks/comms/campaigns/{quarter}/{month}/` | Strategy, calendar, drafts, approved, posted |
| `1-tracks/comms/reports/` | Daily, weekly, A/B test reports |
| `3-knowledge/zettel/` | Topic zettels for content generation |

## Setup

Optional env vars in `.datacore/env/.env`: `X_CONSUMER_KEY`, `X_CONSUMER_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`, `XAI_API_KEY`, `POSTHOG_API_KEY`, `POSTHOG_PROJECT_KEY`, `NEYNAR_API_KEY`, `TELEGRAM_BOT_TOKEN`, `MEDIUM_TOKEN`.

## Boundaries

- Landing page **content** is created here; server **deployment** is handled by the `dev` module.
- Does NOT handle direct email campaigns (see `mail` module).
- Human approval required by default (`auto_post: false`).

---

*This file covers structure, capability, and stable configuration. Learned behavior, user corrections, and operational preferences live as engrams — call `plur_recall_hybrid` for those.*
