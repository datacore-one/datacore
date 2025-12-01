# GTD System Specification

This document defines the structure and routing rules for the GTD system. All GTD agents must follow this specification.

## File Locations

| File | Path | Purpose |
|------|------|---------|
| Inbox | `0-personal/org/inbox.org` | Single capture point, process to zero |
| Next Actions | `0-personal/org/next_actions.org` | Active tasks organized by focus area |
| Someday/Maybe | `0-personal/org/someday.org` | Future possibilities, not committed |
| Habits | `0-personal/org/habits.org` | Recurring behavioral tracking |

## next_actions.org Structure

### Top-Level Tiers (`*` headings)

```
* TIER 1: STRATEGIC FOUNDATION     # Core business, highest priority
* TIER 2: SUPPORTING WORK          # Support systems, operations
* PERSONAL: LIFE & DEVELOPMENT     # Personal wellbeing, growth
* RESEARCH & LEARNING              # Learning pipeline, research queues
```

### Focus Areas (`**` headings)

#### TIER 1: STRATEGIC FOUNDATION
| Focus Area | Description | Keywords |
|------------|-------------|----------|
| `/Verity` | Institutional data marketplace, ZK proof of compute | verity, zkp, data marketplace, institutional |
| `/Mr Data` | AI second brain system development | data system, mr-data, datacore, agents, CLI |
| `/Datafund (Core Operations)` | Core business operations | datafund, operations, DMCC, VARA |
| `/Fundraising` | Investment, pitch, investor relations | fundraising, investors, pitch, DF1, SAFT |
| `/Network & Ecosystem Building` | Partnerships, community | partnerships, ecosystem, community |

#### TIER 2: SUPPORTING WORK
| Focus Area | Description | Keywords |
|------------|-------------|----------|
| `BV (Braveheart Ventures)` | Investment vehicle | braveheart, BV |
| `Swarm` | Swarm network related (winding down) | swarm, ethswarm |

#### PERSONAL: LIFE & DEVELOPMENT
| Focus Area | Description | Keywords |
|------------|-------------|----------|
| `/Personal Development` | Growth, productivity, stoicism | personal dev, productivity, stoicism |
| `/Health & Longevity` | Health optimization, exercise, nutrition | health, longevity, supplements, exercise |
| `Home & Family` | Family, home maintenance | family, home, teo |
| `Learning & Education` | Courses, skills development | learning, courses, skills |
| `Philosophy & Values Practice` | Philosophy, ethics | philosophy, stoicism, ethics |
| `Family & Relationships` | Relationships | family, relationships |
| `Mental Health & Wellbeing` | Mental health | mental health, wellbeing |
| `Financial Management` | Personal finance | finance, budget, taxes |

#### RESEARCH & LEARNING
| Focus Area | Description | Keywords |
|------------|-------------|----------|
| `Verity` | Verity research topics | verity research |
| `Mr Data` | Data system research | mr-data research |
| `Trading` | Trading strategies, market analysis | trading, markets, strategies |
| `Datafund` | Datafund research | datafund research |
| `Business & Strategy` | Business research | business, strategy |
| `Technology & Innovation` | Tech research | technology, innovation |
| `Personal` | Personal interest research | personal research |

## Routing Rules

### Classification by Content

When processing an inbox item, classify by these keywords:

| If content contains... | Route to |
|------------------------|----------|
| verity, ZK, data marketplace, institutional | TIER 1 → /Verity |
| datafund, DMCC, VARA, Dubai, pilot | TIER 1 → /Datafund (Core Operations) |
| investor, pitch, fundraising, SAFT, term sheet | TIER 1 → /Fundraising |
| mr-data, datacore, agents, second brain, CLI | TIER 1 → /Mr Data |
| partnership, ecosystem, community | TIER 1 → /Network & Ecosystem Building |
| trading, position, market, strategy (trading) | RESEARCH → Trading |
| health, supplements, exercise, longevity | PERSONAL → /Health & Longevity |
| family, home, teo | PERSONAL → Home & Family |
| personal growth, productivity, stoicism | PERSONAL → /Personal Development |
| research, read, article, paper, learn | RESEARCH → (match sub-topic) |

### Priority Mapping

| Source Priority | org-mode Priority |
|-----------------|-------------------|
| CRITICAL | `[#A]` + DEADLINE |
| HIGH / Priority A | `[#A]` |
| MEDIUM / Priority B | `[#B]` |
| LOW / Priority C | `[#C]` |

### Task States

| State | Meaning |
|-------|---------|
| `TODO` | Ready to work on |
| `NEXT` | High priority, work on today/immediately |
| `WAITING` | Blocked, waiting on someone/something |
| `DONE` | Completed |
| `CANCELLED` | No longer relevant |

## org-mode Formatting

### Task Structure

```org
*** TODO [#A] Verb-driven task heading
SCHEDULED: <2025-12-02 Mon>
DEADLINE: <2025-12-06 Fri>
:PROPERTIES:
:CREATED: [2025-11-28 Fri]
:SOURCE: Where this came from
:EFFORT: 2h
:PRIORITY: A
:END:

Context paragraph explaining why this matters.

Action items:
- [ ] Specific step 1
- [ ] Specific step 2

Related: [[Wiki Link 1]], [[Wiki Link 2]]
```

### Heading Levels

| Level | Usage |
|-------|-------|
| `*` | Tier (TIER 1, TIER 2, PERSONAL, RESEARCH) |
| `**` | Focus Area (/Verity, /Datafund, etc.) |
| `***` | Individual task or project |
| `****` | Sub-task within a project |

## Processing Checklist

When processing an inbox entry:

1. **Read the entry** - Understand what it is
2. **Classify** - Actionable task, research item, reference, or trash?
3. **Determine focus area** - Match keywords to routing table above
4. **Determine tier** - TIER 1 (strategic), TIER 2 (support), PERSONAL, or RESEARCH
5. **Set priority** - [#A], [#B], [#C] based on importance
6. **Preserve metadata** - Keep existing PROPERTIES, dates, links
7. **Route** - Append to correct section in next_actions.org
8. **Remove from inbox** - Delete entry from inbox.org

## Standing Items (Never Remove)

These items must never be deleted from inbox.org:
- Line 1: `* TODO Do more. With less.`
- The `* Inbox` heading

## Notes Integration

Focus areas map to notes folders in `0-personal/notes/1-active/`:

| Focus Area | Notes Folder |
|------------|--------------|
| /Verity, /Datafund | `1-active/datafund/` |
| /Mr Data | `1-active/mr-data/` |
| Trading | `1-active/trading/` |
| /Health & Longevity | `1-active/health-longevity/` |
| /Personal Development | `1-active/personal-dev/` |
| Home & Family | `1-active/family/` |

Use wiki-links `[[Note Name]]` to connect tasks to relevant notes.
