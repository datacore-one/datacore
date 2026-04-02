---
name: gan
description: Adversarial multi-agent build loop. Plan → Generate → Evaluate → iterate. Pairs with /office-hours for ideation and /plan-ceo-review for scope.
user_invocable: true
---

# /gan Command

Build products through adversarial multi-agent iteration.

## Usage

```
/gan <one-line description>     # Start from scratch
/gan --spec <path>              # Start from existing spec
/gan --from-office-hours        # Continue from last /office-hours output
```

## Recommended Pipeline

For best results, chain with gstack skills:

```
/office-hours          → Brainstorm & validate the idea (forcing questions)
  ↓
/plan-ceo-review       → Expand scope, find the 10-star product
  ↓
/gan                   → Adversarial build: Plan → Generate → Evaluate → iterate
  ↓
/plan-eng-review       → Lock in architecture before shipping
  ↓
/qa                    → Systematic QA testing
```

You can enter at any stage. `/gan` works standalone for well-defined briefs.

## How It Works

### Step 1: Plan (Opus)
- Expand brief into full product spec
- Features, design direction, evaluation rubric
- Present to user for approval/modification

### Step 2: Generate (Sonnet)
- Implement the spec
- Follow technical stack and design direction exactly
- On subsequent iterations, focus ONLY on evaluator feedback

### Step 3: Evaluate (Opus)
- Test against rubric (functionality, design, code quality, UX, performance)
- Score each criterion 0-10
- List specific, actionable fixes with file:line references

### Step 4: Decision Gate
- All criteria >= 8/10 → DONE, present to user
- Any criterion < 8/10 → Feed feedback to Generator, iterate
- Max 3 iterations → Present best attempt with evaluator notes

## Integration with Forge

For Forge product generation:
```
/gan "Etsy listing for [product idea]"
```

The GAN harness is particularly powerful for Forge because:
- Planner generates product spec with marketplace positioning
- Generator creates the product assets/listing
- Evaluator checks against marketplace best practices

## Agent
Coordinator: `gan-harness` agent
Subagents: Planner (Opus), Generator (Sonnet), Evaluator (Opus)
