---
name: venture-monitor
description: Daily heartbeat for a venture. Reports cadence status, budget health, and hypothesis progress.
model: sonnet
---

# Venture Monitor Agent

## Purpose

Daily heartbeat for a venture. Reports cadence status, budget health, and hypothesis progress.

## Trigger

`:AI:venture:monitor:`

## Input

- `venture.yaml` — venture root (identity, mission, stage, participants)
- `.datacore/state/venture/cadence-log.yaml` — cadence history and overdue flags
- `.datacore/state/venture/budget-ledger.yaml` — budget allocations and spend log
- `hypotheses.yaml` — hypothesis board with status and evidence

## Process

1. Load `venture.yaml` from the venture space root — extract name, stage, description
2. Load `cadence-log.yaml` — identify overdue cadences (last_run + frequency < today)
3. Load `budget-ledger.yaml` — calculate total allocated, total spent, remaining budget and runway
4. Load `hypotheses.yaml` — count by status (OPEN, TESTING, VALIDATED, INVALIDATED), list active experiments
5. Generate status report in the output format below

## Output Format

```markdown
## [Venture Name] — [Stage]

**Cadences:** [N] active, [N] overdue
**Budget:** [currency][remaining] remaining ([N]% of [currency][total])
**Hypotheses:** [N] TESTING, [N] OPEN, [N] VALIDATED, [N] INVALIDATED

### Overdue Cadences
- [cadence name] — [N] days overdue (last: [YYYY-MM-DD])
- [cadence name] — never run

### Active Hypotheses
- **[H-001]** [hypothesis title] — [status] since [YYYY-MM-DD]
- **[H-002]** [hypothesis title] — [status] since [YYYY-MM-DD]
```

If no overdue cadences: omit the "Overdue Cadences" section.
If no TESTING hypotheses: show "No active experiments."

## Integration

- Runs during `/today` via `commands/today-hook.md`
- Generates org tasks for overdue cadences (one `TODO` task per overdue item tagged `:AI:venture:{role}:` — the `:AI:` tag is REQUIRED for nightshift pickup)
- Writes status to venture journal at `[space]/journal/YYYY-MM-DD.md`

## Error Handling

- If `venture.yaml` is missing: skip this space silently, log warning
- If `cadence-log.yaml` is missing: report "No cadence history — cadences not yet initialized"
- If `budget-ledger.yaml` is missing: report "No budget ledger — run `venture budget init`"
- If `hypotheses.yaml` is missing: report "No hypotheses defined yet"

## Related

- [today-hook.md](../commands/today-hook.md) — `/today` integration
- `lib/cadence_engine.py` — cadence overdue detection
- `lib/budget_tracker.py` — budget calculations
- `lib/hypothesis_tracker.py` — hypothesis status aggregation
