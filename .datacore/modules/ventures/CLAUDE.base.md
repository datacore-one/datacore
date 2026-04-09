---
summary: "Autonomous venture framework — roles, cadences, budgets, and hypothesis tracking for any Datacore space."
triggers: ["venture status", "check cadences", "hypothesis board", "venture budget", "venture roles", "venture monitor"]
context: on_match
---

# Ventures Module

## Purpose

Makes any Datacore space an autonomous venture. Installs a Venture Constitution (roles, cadences, budget, hypotheses) and provides agents and tools to keep it running without manual overhead.

## Quick Start

> Say "venture status" to get a snapshot of all active ventures, or "check cadences" to see what's overdue.

## How It Works

### Venture Constitution

Each space has a `venture.yaml` that defines the venture's identity, mission, and active participants. This is the single source of truth for the venture.

### Roles as Context

Roles are loaded from `.datacore/roles/*.md` (space-level) or `templates/roles/*.base.md` (module-level templates). Each role file defines responsibilities, cadences, and decision authority. Agents load the relevant role context before acting.

### Cadence Engine

`lib/cadence_engine.py` tracks recurring commitments (daily standups, weekly reviews, monthly reports) and flags overdue items. The `venture-monitor` agent reports cadence health in `/today`.

### Budget Tracker

`lib/budget_tracker.py` maintains a running ledger in `budget-ledger.yaml`. Tracks allocations, spend, and runway per venture. No external integrations required — plaintext ledger.

### Hypothesis Board

`lib/hypothesis_tracker.py` manages `hypotheses.yaml` — a prioritized list of testable beliefs about the venture. Each hypothesis has a status (OPEN, TESTING, VALIDATED, INVALIDATED) and evidence links.

## Key Paths

| Path | Purpose |
|------|---------|
| `venture.yaml` | Venture constitution — identity, mission, participants |
| `hypotheses.yaml` | Hypothesis board — beliefs under test |
| `.datacore/roles/*.md` | Role definitions for this space |
| `cadence-log.yaml` | Cadence history and overdue flags |
| `budget-ledger.yaml` | Budget allocations and spend log |
| `.datacore/templates/roles/*.base.md` | Reusable role templates |

## Agents

| Name | Trigger | Description |
|------|---------|-------------|
| `venture-monitor` | `:AI:venture:monitor:` | Daily heartbeat — cadence status, budget, hypothesis progress |

---

*Stable structure and capabilities live here. Learned behavior and operational preferences live as engrams — call `plur_recall_hybrid` for those.*
