# /learn

Capture an engram into PLUR memory.

## Purpose

`/learn` is the front door to your persistent AI memory. Use it to record corrections, preferences, decisions, conventions, or any reusable knowledge that should survive across sessions and projects.

This command wraps `mcp__plur__plur_learn` with sensible defaults so you can capture in one line without thinking about engram metadata.

> Note: this is the **Datacore /learn** command (PLUR-backed). It explicitly supersedes the gstack `learn` skill. Do not invoke `gstack:learn` — engrams are the canonical learning artifact in this Datacore installation.

## When to use

- User corrects you ("no, use X not Y") → capture the correction immediately
- User states a preference ("always do X", "never do Y") → capture
- You discover a codebase convention or pattern that future sessions should know
- You make a decision worth preserving (architectural, naming, branding)
- You want to remember a competitive insight, market datum, or contact detail
- You realize you got something wrong in this session and want it recorded so you don't repeat it

If unsure whether something is worth saving: it probably is. Engrams are cheap; re-deriving lost context is expensive.

## Usage

### Quick capture

```
/learn The current price of X is €Y as of 2026-05-07
```

The command will:
1. Call `mcp__plur__plur_learn` with the statement
2. Auto-classify type (terminological/behavioral/procedural/architectural) from content
3. Default scope to `global`
4. Return the engram ID

### Structured capture

When the user provides additional metadata in natural language, parse it:

```
/learn [decided] [domain: cos.pricing] €19 entry tier is correct, €200 floor was wrong
```

Recognized inline modifiers:
- `[exploring]` / `[leaning]` / `[decided]` / `[locked]` → commitment level
- `[domain: x.y]` → domain tag
- `[scope: project:name]` → custom scope
- `[type: behavioral|terminological|procedural|architectural]` → explicit type
- `[tags: a,b,c]` → searchable tags
- `[derived_from: ENG-id]` → link to source engram
- `[pinned]` → always-load flag (use sparingly — meta-rules only)

### Forgetting

If the user says "forget X" or "that's wrong", call `mcp__plur__plur_forget` with the matching engram ID instead.

## Workflow

1. Parse the user's input — extract statement and any inline modifiers
2. If no `[type]` modifier, infer:
   - Contains "always", "never", "prefer", "don't" → `behavioral`
   - Defines a name, term, or category → `terminological`
   - Describes a how-to or workflow → `procedural`
   - Describes a system design or architecture → `architectural`
3. If no `[domain]` modifier, infer from content (e.g. mentions trading → `trading.x`, mentions CoS → `cos.x`)
4. Add a `rationale` field if the statement implies a reason
5. Call `mcp__plur__plur_learn`
6. Report the engram ID and what was captured back to the user, briefly

## Output format

```
✓ ENG-2026-MMDD-NNN captured
  type: <type>
  domain: <domain>
  scope: <scope>
  commitment: <commitment>
```

Then a one-line confirmation of what was learned. Don't repeat the full statement back.

## Cross-references

- Memory engine: PLUR (`~/.plur/engrams.yaml`)
- Recall: `mcp__plur__plur_recall_hybrid` for searching engrams
- Feedback: `mcp__plur__plur_feedback` to rate injected engrams during sessions
- Forget: `mcp__plur__plur_forget` to retire stale or wrong engrams
- Session lifecycle: `mcp__plur__plur_session_start` (auto via hooks) and `mcp__plur__plur_session_end`

## Anti-patterns

- Do NOT save full PR descriptions, activity summaries, or git logs — those are derivable from the source. Save only what's *surprising* or *non-obvious*.
- Do NOT save ephemeral state ("currently working on X") — engrams persist; tasks belong in `next_actions.org`.
- Do NOT save things already in CLAUDE.md — context files are loaded automatically.
- Do NOT duplicate. If a similar engram exists, update it via `plur_promote` or `plur_learn` with `derived_from` instead of creating a new one.

## Examples

```
/learn Leo is a poisoned brand for AI products — Brave Leo (privacy AI) and Leo AI ($12.7M, a16z) own it
→ ENG-2026-0507-001 captured (type: terminological, domain: branding)

/learn [decided] [domain: cos.pricing] CoS entry tier should be €19, not €200 — €19 is the universal AI on-ramp
→ ENG-2026-0507-002 captured (type: behavioral, commitment: decided)

/learn The blackpi RPi is at 192.168.1.25 LAN, 100.115.67.71 Tailscale
→ ENG-2026-0507-003 captured (type: terminological, domain: infrastructure)
```
