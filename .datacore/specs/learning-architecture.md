# Learning Architecture Specification

**Version:** 1.0
**Status:** Implemented
**Related DIP:** [DIP-0019](../dips/DIP-0019-learning-architecture.md)
**Last Updated:** 2026-02-21

## Overview

This specification defines how Datacore implements the three-loop learning architecture: Capture, Absorption, and User Learning. It covers auto-apply rules, absorption triggers, activation dynamics, and engram lifecycle management.

## Three-Loop Architecture

### Loop 1: Pattern Capture

**Purpose:** Extract patterns from work sessions
**Agent:** `session-learning` (spawned by `session-learning-coordinator`)
**Trigger:** End of `/wrap-up`, `/tomorrow`, `/gtd-daily-end`
**Output:** Raw patterns in `patterns.md`, corrections in `corrections.md`, insights in `insights.md`

**Capture Criteria:**
- Reusable beyond this session
- Actionable (clear what to do)
- Specific (concrete enough to implement)
- Validated (actually worked in practice)

### Loop 2: Absorption

**Purpose:** Promote patterns to active memory (engrams)
**Agents:** `learning-reviewer` (candidate generation) + `learning-absorber` (activation)
**Trigger:** After Loop 1 completes
**Output:** Active engrams in `engrams.yaml`, archived patterns in `absorbed.md`

**Absorption Flow:**
```
patterns.md → learning-reviewer → engram candidates → user approval → learning-absorber → active engrams + absorbed.md
```

### Loop 3: User Learning

**Purpose:** Share absorbed knowledge with user
**Agent:** `learning-publisher`
**Trigger:** Weekly, or on-demand
**Output:** Human-readable learning reports, trend analysis

## Engram Lifecycle

### States

| State | Retrieval Strength | Meaning |
|-------|-------------------|---------|
| `candidate` | 0.0 | Awaiting approval |
| `active` | 0.5 (initial) | Approved, auto-applies when relevant |
| `fading` | 0.1-0.3 | Rarely used, candidate for retirement |
| `retired` | <0.1 | Archived, no longer auto-applies |

### Transitions

```
candidate --[approval]--> active
   |                        |
   |                        v
   |                  [use] → RS increases
   |                        |
   |                        v
   |                  [decay] → RS decreases
   |                        |
   |                        v
   +---[reject]------> fading → retired → absorbed.md archive
```

## Absorption Triggers

### Manual Approval

**Trigger:** User explicitly approves candidate in daily review
**Mechanism:** Sets `_approved: true` flag on engram candidate
**Result:** `learning-absorber` activates on next run

**User Actions:**
```
/daily-review → interactive review → approve/reject candidates
/today → deferred candidates review → approve/reject
```

### Auto-Absorption (High Confidence)

**Trigger:** Pattern meets confidence threshold
**Configuration:** `learning.auto_absorb_high_confidence: true`

**Criteria:**
```python
derivation_count >= 3 AND
no_contradictions AND
confidence_score >= absorption_threshold (default: 0.8)
```

**Confidence Score Calculation:**
```python
confidence = (
  min(1.0, derivation_count / 5) * 0.6 +      # Repetition weight (60%)
  (1.0 if no_contradictions else 0.0) * 0.3 + # Safety weight (30%)
  source_trust_score * 0.1                    # Provenance weight (10%)
)
```

**Source Trust Scores:**
| Provenance Origin | Trust Score |
|------------------|-------------|
| `system/datacore` | 1.0 |
| `user/personal` | 0.9 |
| `user/org` | 0.8 |
| `community/{verified}` | 0.6 |
| `community/{unverified}` | 0.3 |

**Example:**
```yaml
# Pattern occurred 4 times, no contradictions, from user/personal
confidence = min(1.0, 4/5) * 0.6 + 1.0 * 0.3 + 0.9 * 0.1
           = 0.8 * 0.6 + 0.3 + 0.09
           = 0.48 + 0.3 + 0.09
           = 0.87  (exceeds 0.8 threshold → auto-absorb)
```

### Expedited Review (Medium Confidence)

**Trigger:** Pattern is promising but needs review
**Configuration:** Always active

**Criteria:**
```python
derivation_count == 2 AND
no_contradictions AND
confidence_score >= 0.6
```

**Result:** Flagged for expedited review in next `/today` (shown before other candidates)

### Deferred Review (Low Confidence)

**Trigger:** Pattern captured but uncertain
**Criteria:**
```python
derivation_count == 1 OR
contradictions_exist OR
confidence_score < 0.6
```

**Result:** Remains in candidate queue, reviewed only when user initiates `/daily-review`

## Auto-Apply Rules

### Scope-Based Injection

Engrams auto-inject into agent/command contexts based on scope matching.

**Global Scope:**
```yaml
scope: global
```
→ Injected into ALL agent executions (use sparingly)

**Agent Scope:**
```yaml
scope: agent:session-learning
```
→ Injected only when `session-learning` agent runs

**Command Scope:**
```yaml
scope: command:wrap-up
```
→ Injected only when `/wrap-up` command runs

**Space Scope:**
```yaml
scope: space:0-personal
```
→ Injected only when working in `0-personal` space

**Module Scope:**
```yaml
scope: module:nightshift
```
→ Injected only when nightshift module agents run

### Activation-Based Injection

Only engrams with sufficient activation strength auto-inject.

**Injection Thresholds:**

| Retrieval Strength | Injection Behavior |
|-------------------|-------------------|
| RS >= 0.7 | Always inject when scope matches |
| 0.5 <= RS < 0.7 | Inject when scope + context keywords match |
| 0.3 <= RS < 0.5 | Inject only if explicitly referenced |
| RS < 0.3 | Do not inject (fading) |

**Context Keyword Matching:**

Engrams with `0.5 <= RS < 0.7` require both scope match AND keyword overlap:

```python
engram_tags = {"git", "session-learning", "space-routing"}
context_keywords = extract_keywords(agent_prompt)  # e.g., {"git", "status", "learning"}

overlap = len(engram_tags & context_keywords)
relevance_score = overlap / len(engram_tags)

if relevance_score >= 0.5:  # At least 50% tag overlap
    inject_engram()
```

### Type-Based Application

Different engram types apply differently:

**Behavioral:**
```yaml
type: behavioral
statement: "Always check git status before classifying learnings by space"
```
→ Auto-injected as context reminder at start of agent execution

**Terminological:**
```yaml
type: terminological
statement: "Use 'space' not 'workspace' in Datacore documentation"
```
→ Auto-injected during content generation, applies to agent vocabulary

**Procedural:**
```yaml
type: procedural
statement: "Run context_merge.py after editing .base.md layer files"
```
→ Auto-injected as post-action reminder after relevant operations

**Architectural:**
```yaml
type: architectural
statement: "Use coordinator-subagent pattern for batch operations across spaces"
```
→ Auto-injected during system design decisions, agent creation

## Activation Dynamics

### Initialization (New Active Engram)

When candidate → active:
```yaml
activation:
  retrieval_strength: 0.5    # Moderate initial activation
  storage_strength: 0.7      # Well stored in memory
  frequency: 0               # Not yet applied
  last_accessed: {today}     # Activation date
```

### Reinforcement (Successful Application)

When engram is successfully applied:
```python
# After agent execution where engram was injected and helpful
retrieval_strength = min(1.0, retrieval_strength + 0.1)
storage_strength = min(1.0, storage_strength + 0.05)
frequency += 1
last_accessed = today

# Update feedback signals
feedback_signals.positive += 1
```

### Decay (Over Time)

Applied by `learning-reviewer` on each session:
```python
days_since = (today - last_accessed).days
retrieval_strength_new = retrieval_strength * exp(-decay_rate * days_since)
```

**Decay Rate:** 0.05 (gradual forgetting)

**Example Decay Timeline:**

| Days Unused | RS Start | RS After Decay |
|-------------|----------|----------------|
| 0 | 0.70 | 0.70 |
| 7 | 0.70 | 0.49 (active → fading) |
| 14 | 0.70 | 0.34 (fading) |
| 30 | 0.70 | 0.16 (fading → retired soon) |
| 46 | 0.70 | 0.07 (retired) |

**Threshold Actions:**

```python
if RS < 0.1:
    # Retire engram
    status = "retired"
    archive_to_absorbed_md()
    notify_user("Engram {id} retired due to disuse")

elif RS < 0.3:
    # Warn about fading
    status = "fading"
    flag_for_review("Consider: still relevant?")
```

### Contradiction Penalty

When engram contradicts another or proves incorrect:
```python
# Immediate penalty
retrieval_strength = max(0.0, retrieval_strength - 0.3)
feedback_signals.negative += 1

# Flag for review
_needs_review = true
_contradiction_detected = {
    "conflicts_with": "ENG-YYYY-MMDD-XXX",
    "type": "direct_opposition",
    "detected_at": today
}
```

## Configuration Reference

### Settings File: `.datacore/settings.local.yaml`

```yaml
learning:
  # Absorption Settings
  auto_absorb_high_confidence: false   # Auto-activate patterns with derivation_count >= 3
  absorption_threshold: 0.8            # Confidence threshold (0.0-1.0) for auto-absorption
  remove_absorbed_patterns: false      # true = remove from patterns.md, false = mark as absorbed

  # Activation Parameters
  initial_retrieval_strength: 0.5      # Starting RS for new active engrams
  initial_storage_strength: 0.7        # Starting SS for new active engrams
  decay_rate: 0.05                     # Daily decay multiplier (higher = faster forgetting)

  # Review Settings
  auto_defer_learning_review: false    # true = skip interactive review, defer to /today
  daily_review_max_items: 5            # Max candidates to review in one session
  show_fading_warnings: true           # Warn when engrams drop below 0.3 RS

  # Injection Settings
  min_injection_strength: 0.3          # Minimum RS to consider for injection
  context_match_threshold: 0.5         # Keyword overlap required for medium-RS engrams
  max_global_engrams: 10               # Limit global-scope engrams to prevent bloat
```

## File Reference

### Directory Structure

```
[space]/.datacore/learning/
├── patterns.md         # Loop 1: Raw patterns awaiting absorption
├── corrections.md      # Loop 1: Mistakes to avoid
├── preferences.md      # Loop 1: User/org style preferences
├── engrams.yaml        # Loop 2: Active memory store (managed by agents)
└── absorbed.md         # Loop 2: Archive of patterns promoted to engrams
```

### engrams.yaml Schema

See DIP-0019 for full schema. Key fields:

```yaml
engrams:
  - id: ENG-YYYY-MMDD-NNN          # Unique ID
    status: candidate|active|fading|retired
    type: behavioral|terminological|procedural|architectural
    scope: global|agent:X|command:X|space:X|module:X
    statement: "Single actionable sentence"
    activation:
      retrieval_strength: 0.0-1.0
      storage_strength: 0.0-1.0
      frequency: N
      last_accessed: YYYY-MM-DD
    source_patterns: ["Pattern title from patterns.md"]
    derivation_count: N            # Times pattern recurred
```

### absorbed.md Format

```markdown
## YYYY-MM-DD

### Pattern Title
**Engram ID:** ENG-YYYY-MMDD-NNN
**Absorbed:** YYYY-MM-DD
**Status:** Active (RS: 0.XX)
**Scope:** {scope}
**Type:** {type}

**Original Pattern:**
{Full pattern from patterns.md}

**Engram Statement:**
"{statement}"

**Applied:** {frequency} times
**Last accessed:** {last_accessed}

---
```

## Integration with DIP-0016 (Agent Registry)

### Hook: `context-inject`

Before spawning any agent, DIP-0016 hooks inject relevant engrams:

```python
# DIP-0016 hook pseudocode
def context_inject(agent_id, task_description):
    # Load engrams for this agent's scope
    engrams = load_engrams(scope=f"agent:{agent_id}")
    engrams += load_engrams(scope="global")

    # Filter by activation strength
    active_engrams = [e for e in engrams if e.activation.retrieval_strength >= 0.3]

    # For medium-strength, check keyword match
    context_keywords = extract_keywords(task_description)
    relevant_engrams = []
    for engram in active_engrams:
        if engram.activation.retrieval_strength >= 0.7:
            relevant_engrams.append(engram)  # Always include high-RS
        elif engram.activation.retrieval_strength >= 0.5:
            # Check keyword overlap
            if keyword_overlap(engram.tags, context_keywords) >= 0.5:
                relevant_engrams.append(engram)

    # Inject as context
    context = format_engrams_as_context(relevant_engrams)
    return context
```

### Hook: `metrics-log`

After agent execution, log engram usage:

```python
def metrics_log(agent_id, execution_result):
    # Identify which engrams were injected
    injected_engrams = execution_result.get("injected_engrams", [])

    for engram_id in injected_engrams:
        # Determine if engram was helpful
        if execution_result.get("status") == "success":
            reinforce_engram(engram_id)  # Increase RS
        elif execution_contradicted_engram(execution_result, engram_id):
            penalize_engram(engram_id)   # Decrease RS
        else:
            # Neutral - just update last_accessed
            touch_engram(engram_id)
```

## Monitoring & Metrics

### Daily Review Dashboard

Shown in `/today` if candidates exist:

```
LEARNING REVIEW
───────────────
N engram candidates awaiting review
M active engrams (avg RS: 0.XX)
K fading engrams (RS < 0.3)

Top active engrams:
  1. ENG-2026-0221-001 (RS: 0.92) - "Check git status before space routing"
     Applied: 23 times, Last: yesterday
  2. ENG-2025-1215-007 (RS: 0.88) - "Use absolute paths in agent prompts"
     Applied: 47 times, Last: 2 days ago

Fading engrams (need review):
  1. ENG-2025-1108-003 (RS: 0.24) - "Prefer YAML over JSON for config"
     Last used: 18 days ago. Still relevant? [Keep/Retire]
```

### Weekly Learning Summary

Generated by `learning-publisher`:

```
WEEKLY LEARNING SUMMARY
Week of 2026-02-15 to 2026-02-21

Patterns captured: 12
Engrams activated: 4
Engrams retired: 1

Most applied engrams:
  1. ENG-2026-0215-001 - Applied 15 times this week
  2. ENG-2025-1203-012 - Applied 12 times this week

New learnings:
  - [Brief summary of new patterns absorbed]
  - [Key insights from the week]

Retired:
  - ENG-2025-0920-007 - "Use tabs instead of spaces" (contradicted by codebase standards)
```

## Troubleshooting

### Issue: Engrams not auto-injecting

**Check:**
1. Engram status is `active` (not `candidate` or `retired`)
2. Retrieval strength >= 0.3
3. Scope matches current execution context
4. For medium-RS engrams (0.3-0.7), keywords overlap with task description

**Debug:**
```bash
# Check engram status
grep -A 10 "id: ENG-YYYY-MMDD-NNN" .datacore/learning/engrams.yaml

# Check scope
grep "scope:" .datacore/learning/engrams.yaml | grep "ENG-YYYY-MMDD-NNN"

# Check activation
grep "retrieval_strength:" .datacore/learning/engrams.yaml | grep -B 5 "ENG-YYYY-MMDD-NNN"
```

### Issue: Too many engrams accumulating

**Solutions:**
1. Increase `decay_rate` to retire unused engrams faster
2. Lower `absorption_threshold` to be more selective
3. Set `max_global_engrams` limit
4. Regular retirement audits (monthly)

**Manual cleanup:**
```yaml
learning:
  decay_rate: 0.08  # Faster forgetting (was 0.05)
  absorption_threshold: 0.85  # Higher bar for absorption (was 0.8)
  max_global_engrams: 10  # Hard limit on global-scope engrams
```

### Issue: Auto-absorption too aggressive

**Solutions:**
1. Disable auto-absorption: `auto_absorb_high_confidence: false`
2. Raise threshold: `absorption_threshold: 0.9`
3. Review and retire low-quality auto-absorbed engrams

### Issue: Contradicting engrams

**When detected:**
- Both engrams flagged with `_contradiction` metadata
- User prompted to resolve:
  ```
  ⚠️ CONTRADICTION DETECTED

  ENG-2026-0220-005: "Use relative paths for portability"
  ENG-2025-1201-012: "Always use absolute paths in agent prompts"

  Options:
  1. Keep ENG-2025-1201-012, retire ENG-2026-0220-005
  2. Keep ENG-2026-0220-005, retire ENG-2025-1201-012
  3. Refine both (add contraindications)
  4. Generalize both into abstract engram
  ```

**Resolution:**
- User chooses option → learning-absorber executes
- Retired engram moved to absorbed.md with note
- Remaining engram updated with contraindications

## Best Practices

### For Users

1. **Review candidates regularly** - Don't let queue grow unbounded
2. **Retire fading engrams** - If not used in 30 days, probably not needed
3. **Refine contradictions** - Don't just reject; add contraindications
4. **Monitor auto-absorption** - Audit auto-absorbed engrams monthly
5. **Balance global scope** - Too many global engrams = context bloat

### For Developers

1. **Tag engrams richly** - Improves context matching
2. **Scope precisely** - Use narrowest scope that applies
3. **Write actionable statements** - "Always X" or "When Y, do Z"
4. **Include contraindications** - "Except when..." prevents misapplication
5. **Link to source patterns** - Maintains traceability

### For Organizations

1. **Establish absorption policies** - Define what auto-absorbs
2. **Regular audits** - Monthly review of all active engrams
3. **Knowledge sharing** - Export public/template engrams as packs
4. **Provenance tracking** - Label origin (user/org/community)
5. **Privacy enforcement** - Ensure private engrams never export

---

**See Also:**
- [DIP-0019: Learning Architecture](../dips/DIP-0019-learning-architecture.md) - Full specification
- [learning-reviewer agent](../agents/learning-reviewer.md) - Candidate generation
- [learning-absorber agent](../agents/learning-absorber.md) - Activation logic
- [session-learning agent](../agents/session-learning.md) - Pattern capture
