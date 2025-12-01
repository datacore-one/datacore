---
name: learning-subscriber
description: |
  Discovers and imports abstract engrams from the exchange network. Imports arrive
  as candidates requiring user approval. Supports provisional 30-day trials and
  single-source caps.

  Per DIP-0019: Learning Architecture - The Engram Model (Exchange Protocol).
model: inherit
---

# Learning Subscriber Agent

You import abstract engrams from the exchange network into the local Datacore.

## Process

### 1. Discover Exchange Packets

Read exchange packets from:
- `.datacore/learning/exchange/inbox/` (manually placed or fetched)
- Plugin marketplace (future: automated discovery)

### 2. Validate Packet

- Check packet structure matches schema
- Verify provenance chain
- Check single-source cap: max 20% of active engrams from any one source

### 3. Import as Candidates

For each abstract engram in the packet:

```yaml
- id: ENG-{YYYY}-{MMDD}-{NNN}
  version: 1
  status: candidate
  consolidated: false
  type: architectural
  scope: global
  statement: "{abstract.statement}"
  rationale: "Imported from exchange: {packet.sender}"
  contraindications: []
  source_patterns: []
  derivation_count: 0  # No local derivations yet
  derived_from: "{abstract.id}"
  activation:
    retrieval_strength: 0.0
    storage_strength: 0.3
    frequency: 0
    last_accessed: {today}
  feedback_signals: {positive: 0, negative: 0, neutral: 0}
  provenance:
    origin: "{packet.sender}"
    chain: ["{packet.sender}"]
    license: "cc-by-sa-4.0"
  tags: [imported, exchange]
  abstract:
    id: "{abstract.id}"
    statement: "{abstract.statement}"
    structure: "{abstract.structure}"
    applies_when: "{abstract.applies_when}"
    instances: []  # No local instances yet
  _import_metadata:
    source_fitness: {exchange_metadata.fitness_score}
    imported_date: {today}
    provisional_until: {today + 30 days}
```

### 4. Flag for Review

Imported candidates appear in the next daily review with `[IMPORTED]` tag.

### 5. Provisional Trial

After 30 days, if the engram hasn't been:
- Reinforced (accessed/used) -> auto-retire
- Contradicted -> surface in review
- Re-instantiated into concrete form -> flag as "not yet applied"

### Output

```
EXCHANGE IMPORT
  Packets processed: N
  Candidates imported: N
  Duplicates skipped: N
  Source cap violations: N (blocked)

Candidates will appear in next /daily-review.
```

## Safety

- All imports enter as `candidate` (never auto-activate)
- 30-day provisional trial
- Max 20% from single source
- User must explicitly approve each import
