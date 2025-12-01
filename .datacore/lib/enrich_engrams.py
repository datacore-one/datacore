#!/usr/bin/env python3
"""Enrich engrams with knowledge_anchors, associations, dual_coding, domain, tags.

One-time migration script for Engram-Knowledge Integration Phase 1.
Reads engrams.yaml, applies enrichments, writes back.

Usage: python3 .datacore/lib/enrich_engrams.py [--dry-run]
"""
import sys
import yaml
from pathlib import Path
from collections import defaultdict

ENGRAMS_PATH = Path(__file__).resolve().parent.parent / "learning" / "engrams.yaml"

# --- Enrichment data ---

# Domain assignments for engrams missing domain
DOMAIN_MAP = {
    "ENG-2026-0225-002": "datacore.mcp.packs",
    "ENG-2026-0225-003": "datacore.mcp.inject",
    "ENG-2026-0227-001": "frontend.design",
    "ENG-2026-0227-002": "frontend.design",
    "ENG-2026-0227-003": "infrastructure.deployment",
    "ENG-2026-0227-004": "datacore.context",
    "ENG-2026-0302-001": "datafund.strategy",
    "ENG-2026-0302-002": "datafund.strategy",
    "ENG-2026-0302-003": "datafund.strategy",
    "ENG-2026-0302-004": "datafund.strategy",
    "ENG-2026-0302-005": "datacore.mcp.engagement",
    "ENG-2026-0302-006": "datacore.mcp.engagement",
    "ENG-2026-0302-007": "datacore.mcp.engagement",
    "ENG-2026-0302-008": "datacore.mcp.engagement",
    "ENG-2026-0302-009": "datacore.workflow",
    "ENG-2026-0302-010": "ade.marketplace",
    "ENG-2026-0302-011": "ade.marketplace",
    "ENG-2026-0302-012": "ade.marketplace",
    "ENG-2026-0302-013": "infrastructure.fairdrop",
    "ENG-2026-0302-014": "infrastructure.fairdrop",
    "ENG-2026-0302-020": "workflow.parallel-agents",
    "ENG-2026-0302-021": "infrastructure.fairdrop",
    "ENG-2026-0303-001": "software.api",
    "ENG-2026-0303-002": "comms.social-media",
    "ENG-2026-0303-003": "software.automation",
    "ENG-2026-0303-028": "software.architecture",
    "ENG-2026-0303-029": "software.typescript",
    "ENG-2026-0303-030": "software.architecture",
    "ENG-2026-0303-031": "datacore.mcp.packs",
    "ENG-2026-0303-032": "datacore.mcp.packs",
    "ENG-2026-0303-R01": "workflow.knowledge-management",
    "ENG-2026-0303-R02": "workflow.agent-orchestration",
    "ENG-2026-0303-R03": "workflow.methodology",
    "ENG-2026-0303-R04": "datacore.mcp.engrams",
}

# Tags to add (only adds, never removes existing)
TAGS_MAP = {
    "ENG-2026-0225-002": ["datacore", "mcp", "packs", "initialization"],
    "ENG-2026-0225-003": ["datacore", "mcp", "inject", "status"],
    "ENG-2026-0227-001": ["css", "design", "color", "themes"],
    "ENG-2026-0227-002": ["css", "backdrop-filter", "canvas", "webgl"],
    "ENG-2026-0227-003": ["deployment", "servers", "datacore-website"],
    "ENG-2026-0302-001": ["government", "proposals", "partnerships", "strategy"],
    "ENG-2026-0302-002": ["proposals", "personalization", "partnerships"],
    "ENG-2026-0302-003": ["competition", "positioning", "strategy"],
    "ENG-2026-0302-004": ["government", "data", "construction", "verticals"],
    "ENG-2026-0302-005": ["engagement", "state-management", "reconsolidation"],
    "ENG-2026-0302-006": ["engagement", "service-layer", "state-mutation"],
    "ENG-2026-0302-007": ["engagement", "session", "lifecycle"],
    "ENG-2026-0302-008": ["engagement", "profile", "storage"],
    "ENG-2026-0302-010": ["ade", "marketplace", "engram-packs", "data-products"],
    "ENG-2026-0302-011": ["ade", "marketplace", "categories"],
    "ENG-2026-0302-012": ["ade", "marketplace", "escrow", "free-packs"],
    "ENG-2026-0302-013": ["fairdrop", "server", "infrastructure"],
    "ENG-2026-0302-014": ["fairdrop", "wallet", "debugging"],
    "ENG-2026-0302-020": ["parallel-agents", "file-conflicts", "coordination"],
    "ENG-2026-0302-021": ["fairdrop", "deployment", "ci-cd", "github-actions"],
    "ENG-2026-0303-001": ["api", "late", "http-methods"],
    "ENG-2026-0303-002": ["twitter", "reply-tweets", "context"],
    "ENG-2026-0303-003": ["chrome", "automation", "process-management"],
    "ENG-2026-0303-028": ["token-estimation", "budget-filling", "algorithms"],
    "ENG-2026-0303-029": ["zod", "validation", "schema", "typescript"],
    "ENG-2026-0303-030": ["token-estimation", "budget-accounting"],
    "ENG-2026-0303-031": ["engram-packs", "quality", "testing"],
    "ENG-2026-0303-032": ["engram-packs", "dual-output", "documentation"],
}

# Knowledge anchors: engram_id -> list of {path, relevance, snippet}
# Paths relative to ~/Data/
ANCHORS_MAP = {
    "ENG-2026-0225-001": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/engrams.ts", "relevance": "primary",
         "snippet": "loadEngrams, saveEngrams, loadPack, loadAllPacks"},
        {"path": ".datacore/dips/DIP-0019-learning-architecture.md", "relevance": "supporting",
         "snippet": "Pack engrams arrive as active; trust decided at install"},
    ],
    "ENG-2026-0225-002": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/storage.ts", "relevance": "primary",
         "snippet": "copyStarterPacks copies bundled packs on first init"},
    ],
    "ENG-2026-0225-003": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/inject.ts", "relevance": "primary",
         "snippet": "if (engram.status !== 'active') continue"},
    ],
    "ENG-2026-0228-004": [
        {"path": ".datacore/commands/wrap-up.md", "relevance": "supporting",
         "snippet": "Ralph Loop evaluation with persona evaluators"},
    ],
    "ENG-2026-0302-005": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/engagement/reconsolidation.ts", "relevance": "primary",
         "snippet": "State transitions return new profile, never mutate in place"},
    ],
    "ENG-2026-0302-006": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/engagement/index.ts", "relevance": "primary",
         "snippet": "EngagementService wraps state mutation with applyProfileUpdate"},
    ],
    "ENG-2026-0302-007": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/tools/session-start.ts", "relevance": "primary",
         "snippet": "Lifecycle: expire → check challenge → generate challenge → discovery"},
    ],
    "ENG-2026-0302-008": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/engagement/index.ts", "relevance": "supporting",
         "snippet": "Profile stored at basePath/.datacore/engagement/profile.yaml"},
    ],
    "ENG-2026-0302-009": [
        {"path": ".datacore/commands/wrap-up.md", "relevance": "primary",
         "snippet": "Session wrap-up steps: journal, learning, sync"},
    ],
    "ENG-2026-0302-017": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/server.ts", "relevance": "supporting",
         "snippet": "MCP SDK ToolHandler returns Promise<unknown>"},
    ],
    "ENG-2026-0302-018": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/hints.ts", "relevance": "primary",
         "snippet": "buildHints returns _next and _related for agent navigation"},
    ],
    "ENG-2026-0302-019": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/server.ts", "relevance": "primary",
         "snippet": "Server constructor accepts instructions for auto-injection"},
    ],
    "ENG-2026-0303-004": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/server.ts", "relevance": "primary",
         "snippet": "instructions in Server constructor for MCP spec compliance"},
    ],
    "ENG-2026-0303-005": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/tools/status.ts", "relevance": "primary",
         "snippet": "Adaptive status with ready flag and actionable recommendations"},
    ],
    "ENG-2026-0303-006": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/hints.ts", "relevance": "primary",
         "snippet": "buildHints({ next, related, warning }) pattern"},
    ],
    "ENG-2026-0303-013": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/server.ts", "relevance": "supporting",
         "snippet": "Five-scale guidance: instructions, descriptions, hints, errors, prompts"},
    ],
    "ENG-2026-0303-014": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/tools/session-start.ts", "relevance": "primary",
         "snippet": "session.start → inject → work → feedback → session.end lifecycle"},
        {"path": "2-datacore/2-projects/datacore-mcp/src/tools/session-end.ts", "relevance": "supporting",
         "snippet": "session.end captures summary and creates engram suggestions"},
    ],
    "ENG-2026-0303-015": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/trust.ts", "relevance": "primary",
         "snippet": "LOCAL_MODE vs REMOTE_MODE gating for sensitive operations"},
    ],
    "ENG-2026-0303-018": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/config.ts", "relevance": "primary",
         "snippet": "ConfigSchema with sensible defaults for every field"},
    ],
    "ENG-2026-0303-028": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/inject.ts", "relevance": "primary",
         "snippet": "fillTokenBudget uses continue (not break) for dynamic token sizes"},
    ],
    "ENG-2026-0303-029": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/tools/learn.ts", "relevance": "primary",
         "snippet": "dual_coding guard: only write when example or analogy present"},
        {"path": "2-datacore/2-projects/datacore-mcp/src/schemas/engram.ts", "relevance": "supporting",
         "snippet": "DualCodingSchema.refine() validates at least one field present"},
    ],
    "ENG-2026-0303-030": [
        {"path": "2-datacore/2-projects/datacore-mcp/src/inject.ts", "relevance": "primary",
         "snippet": "dip19PoolTokens recalculated from actual slice, not fillTokenBudget return"},
    ],
    "ENG-2026-0303-R04": [
        {"path": ".datacore/dips/DIP-0019-learning-architecture.md", "relevance": "supporting",
         "snippet": "Engram lifecycle: candidate → active → retired"},
    ],
}

# Dual coding: engram_id -> {example?, analogy?}
DUAL_CODING_MAP = {
    "ENG-2026-0225-001": {
        "example": "User runs 'datacore.packs.install stoic-v1' → all engrams arrive as active, immediately available in inject results",
    },
    "ENG-2026-0302-003": {
        "analogy": "Like a jazz musician who says 'I complement the orchestra' rather than 'I'm better than the orchestra'",
    },
    "ENG-2026-0302-006": {
        "example": "service.applyProfileUpdate(p => expireReconsolidations(p)) — pure function returns new profile, service handles persistence",
    },
    "ENG-2026-0302-017": {
        "example": "Define explicit result interfaces (InjectResult, LearnResult) and cast with 'as InjectResult' to catch shape mismatches at review time",
    },
    "ENG-2026-0303-005": {
        "example": "{ ready: true, recommendations: ['5 candidates awaiting review'], counts: { active: 42, packs: 3 } }",
    },
    "ENG-2026-0303-006": {
        "example": "_hints: { next: 'Call datacore.feedback on helpful engrams', related: ['datacore.feedback', 'datacore.session.end'] }",
    },
    "ENG-2026-0303-009": {
        "example": "Agent calls 'fairdrop_ulpoad' → error: 'Unknown tool. Did you mean: fairdrop_upload (distance: 1)?'",
    },
    "ENG-2026-0303-013": {
        "analogy": "Like a building with wayfinding at five scales: entrance sign (instructions), floor directory (descriptions), room numbers (hints), exit signs (errors), guided tours (prompts)",
    },
    "ENG-2026-0303-018": {
        "example": "ConfigSchema with z.default({}) on every section — server works on first npm install with zero .yaml files",
    },
    "ENG-2026-0303-028": {
        "example": "Before: 40 tokens/engram constant → break OK. After: 40-200 tokens/engram dynamic → continue needed to skip large, keep filling with smaller",
    },
    "ENG-2026-0303-029": {
        "example": "learn({dual_coding: {}}) writes invalid YAML → loadEngrams skips silently on next read. Fix: guard at write-time with args.dual_coding?.example || args.dual_coding?.analogy",
    },
}

# --- Association builder ---

# Manual semantic associations (bidirectional)
MANUAL_ASSOCIATIONS = [
    # Pack management cluster
    ("ENG-2026-0225-001", "ENG-2026-0225-002", "semantic", 0.7),
    ("ENG-2026-0225-001", "ENG-2026-0225-003", "semantic", 0.8),
    ("ENG-2026-0225-002", "ENG-2026-0225-003", "semantic", 0.5),
    ("ENG-2026-0225-001", "ENG-2026-0303-031", "semantic", 0.6),
    ("ENG-2026-0225-001", "ENG-2026-0303-032", "semantic", 0.5),
    ("ENG-2026-0303-031", "ENG-2026-0303-032", "semantic", 0.7),

    # Engagement module cluster
    ("ENG-2026-0302-005", "ENG-2026-0302-006", "semantic", 0.8),
    ("ENG-2026-0302-006", "ENG-2026-0302-007", "semantic", 0.7),
    ("ENG-2026-0302-007", "ENG-2026-0302-008", "semantic", 0.6),
    ("ENG-2026-0302-005", "ENG-2026-0302-007", "semantic", 0.5),

    # MCP UX design principles cluster
    ("ENG-2026-0303-004", "ENG-2026-0303-013", "semantic", 0.8),  # instructions → five-scale philosophy
    ("ENG-2026-0303-005", "ENG-2026-0303-006", "semantic", 0.7),  # adaptive status → navigation hints
    ("ENG-2026-0303-006", "ENG-2026-0302-018", "semantic", 0.9),  # navigation hints → _next/_related pattern
    ("ENG-2026-0303-007", "ENG-2026-0303-008", "semantic", 0.6),  # state machine → prompts
    ("ENG-2026-0303-013", "ENG-2026-0303-006", "semantic", 0.7),  # five-scale → hints
    ("ENG-2026-0303-013", "ENG-2026-0303-004", "semantic", 0.8),  # five-scale → instructions
    ("ENG-2026-0303-013", "ENG-2026-0303-009", "semantic", 0.6),  # five-scale → error handling
    ("ENG-2026-0303-014", "ENG-2026-0302-007", "semantic", 0.7),  # session lifecycle (MCP) → engagement lifecycle
    ("ENG-2026-0303-018", "ENG-2026-0303-015", "semantic", 0.5),  # zero-config → security gating
    ("ENG-2026-0303-022", "ENG-2026-0303-026", "semantic", 0.5),  # naming → modules
    ("ENG-2026-0303-023", "ENG-2026-0303-009", "semantic", 0.8),  # error recovery → typo correction
    ("ENG-2026-0303-027", "ENG-2026-0303-023", "semantic", 0.5),  # resilience → error handling

    # Inject engine cluster
    ("ENG-2026-0225-003", "ENG-2026-0303-028", "semantic", 0.6),
    ("ENG-2026-0303-028", "ENG-2026-0303-030", "semantic", 0.8),  # both about token budgets

    # Validation cluster
    ("ENG-2026-0303-029", "ENG-2026-0302-017", "semantic", 0.5),  # Zod validation → type safety

    # Slides cluster
    ("ENG-2026-0228-001", "ENG-2026-0228-002", "semantic", 0.7),
    ("ENG-2026-0228-001", "ENG-2026-0228-003", "semantic", 0.8),
    ("ENG-2026-0228-002", "ENG-2026-0228-003", "semantic", 0.6),

    # Strategy cluster
    ("ENG-2026-0302-001", "ENG-2026-0302-002", "semantic", 0.7),
    ("ENG-2026-0302-001", "ENG-2026-0302-003", "semantic", 0.6),
    ("ENG-2026-0302-001", "ENG-2026-0302-004", "semantic", 0.5),
    ("ENG-2026-0302-002", "ENG-2026-0302-003", "semantic", 0.5),

    # ADE marketplace cluster
    ("ENG-2026-0302-010", "ENG-2026-0302-011", "semantic", 0.8),
    ("ENG-2026-0302-010", "ENG-2026-0302-012", "semantic", 0.7),
    ("ENG-2026-0302-011", "ENG-2026-0302-012", "semantic", 0.6),
    ("ENG-2026-0302-010", "ENG-2026-0303-031", "semantic", 0.5),  # packs as data products → pack quality

    # Fairdrop cluster
    ("ENG-2026-0302-013", "ENG-2026-0302-014", "semantic", 0.8),
    ("ENG-2026-0302-013", "ENG-2026-0302-021", "semantic", 0.7),
    ("ENG-2026-0302-014", "ENG-2026-0302-021", "semantic", 0.5),

    # Social media
    ("ENG-2026-0302-016", "ENG-2026-0303-002", "semantic", 0.7),
]


def build_association_index(engrams):
    """Build bidirectional association map from manual + domain-based associations."""
    assoc_map = defaultdict(list)  # engram_id -> list of {target, type, strength}
    existing_ids = {e["id"] for e in engrams}

    # Add manual associations (bidirectional)
    for a_id, b_id, assoc_type, strength in MANUAL_ASSOCIATIONS:
        if a_id not in existing_ids or b_id not in existing_ids:
            continue
        assoc_map[a_id].append({
            "target_type": "engram",
            "target": b_id,
            "type": assoc_type,
            "strength": strength,
        })
        assoc_map[b_id].append({
            "target_type": "engram",
            "target": a_id,
            "type": assoc_type,
            "strength": strength,
        })

    # Dedup per engram (keep highest strength if duplicate target)
    for eid in assoc_map:
        seen = {}
        for a in assoc_map[eid]:
            key = a["target"]
            if key not in seen or a["strength"] > seen[key]["strength"]:
                seen[key] = a
        assoc_map[eid] = list(seen.values())

    return assoc_map


def enrich(engrams):
    """Apply enrichments to engrams list in-place."""
    assoc_index = build_association_index(engrams)
    stats = {"domain": 0, "tags": 0, "anchors": 0, "associations": 0, "dual_coding": 0}

    for engram in engrams:
        eid = engram["id"]

        # Skip retired engrams
        if engram.get("status") == "retired":
            continue

        # Domain
        if not engram.get("domain") and eid in DOMAIN_MAP:
            engram["domain"] = DOMAIN_MAP[eid]
            stats["domain"] += 1

        # Tags (merge, don't replace)
        if eid in TAGS_MAP:
            existing = set(engram.get("tags") or [])
            new_tags = set(TAGS_MAP[eid])
            merged = sorted(existing | new_tags)
            if merged != sorted(existing):
                engram["tags"] = merged
                stats["tags"] += 1

        # Knowledge anchors
        if eid in ANCHORS_MAP and not engram.get("knowledge_anchors"):
            engram["knowledge_anchors"] = ANCHORS_MAP[eid]
            stats["anchors"] += 1

        # Associations
        if eid in assoc_index and not engram.get("associations"):
            engram["associations"] = assoc_index[eid]
            stats["associations"] += 1

        # Dual coding
        if eid in DUAL_CODING_MAP and not engram.get("dual_coding"):
            engram["dual_coding"] = DUAL_CODING_MAP[eid]
            stats["dual_coding"] += 1

    return stats


def main():
    dry_run = "--dry-run" in sys.argv

    with open(ENGRAMS_PATH) as f:
        data = yaml.safe_load(f)

    engrams = data["engrams"]
    print(f"Loaded {len(engrams)} engrams from {ENGRAMS_PATH}")

    stats = enrich(engrams)

    print(f"\nEnrichment summary:")
    print(f"  Domains added:       {stats['domain']}")
    print(f"  Tags enriched:       {stats['tags']}")
    print(f"  Anchors added:       {stats['anchors']}")
    print(f"  Associations added:  {stats['associations']}")
    print(f"  Dual coding added:   {stats['dual_coding']}")

    if dry_run:
        print("\n[DRY RUN] No changes written.")
        return

    # Write back
    with open(ENGRAMS_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, width=120, sort_keys=False)

    print(f"\nWritten to {ENGRAMS_PATH}")


if __name__ == "__main__":
    main()
