# Intent Routing

Deterministic intent scoring for GTD tasks. Maps tasks to strategic intents from the Intent Graph using keyword + tag overlap.

## Intent Graph (extracted)

The intent graph has 5 top-level intents:

| # | Intent | Keywords |
|---|--------|----------|
| 1 | Augments human intelligence | learning, engram, pattern, knowledge, insight, extraction, session, PKM, zettel, briefing, first-run, onboarding |
| 2 | Runs as autonomous organization | nightshift, autonomous, pipeline, content, publish, analytics, evaluate, CLOCK, invoice, financial, tracking |
| 3 | Financially sustainable | exchange, commission, revenue, token, marketplace, budget, cost, MiCA, service, provider, subscription |
| 4 | Empowering and worth sharing | developer, experience, documentation, tutorial, plugin, extension, community, DX, viral, privacy, sovereign |
| 5 | Living collective intelligence | ecosystem, network, agent-to-agent, embedding, provenance, Swarm, collective, exchange, tap-in, community |

## Tag Bonuses

| Tag Pattern | Intent | Bonus |
|-------------|--------|-------|
| `:AI:research:` | 1 (Build Knowledge) | +0.3 |
| `:AI:content:` | 2 (Create Value) | +0.3 |
| `:AI:data:` | 2 (Autonomous Ops) | +0.2 |
| `:AI:pm:` | 2 (Autonomous Ops) | +0.2 |
| `:AI:code:` | 4 (Developer Experience) | +0.2 |

## Scoring Algorithm

For each task:

1. Tokenize title + description into lowercase words, remove stopwords
2. For each intent, count keyword overlaps / total keywords = match_ratio
3. Apply tag bonus if tag matches an intent
4. Multi-parent bonus: if match_ratio > 0.2 for 2+ intents, score += 2
5. Final score = max(match_ratio * 8 + bonuses, 10), clamped 0-10
6. Return highest-scoring intent + score + reasoning

## Usage

This skill is loaded by `gtd-inbox-processor` during task classification and by `queue-optimizer` during nightshift queue building. The `nightshift.intent_score` MCP tool implements the algorithm.

## Multi-Intent Priority

Tasks serving multiple intents are high-leverage (per Intent Graph). The multi-parent bonus (+2) ensures these tasks float to the top of the queue.
