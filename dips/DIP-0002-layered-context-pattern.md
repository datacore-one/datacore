# DIP-0002: Layered Context Pattern

| Field | Value |
|-------|-------|
| **DIP** | 0002 |
| **Title** | Layered Context Pattern |
| **Author** | Datacore Team |
| **Type** | Core |
| **Status** | Draft |
| **Created** | 2025-12-01 |

## Summary

A universal pattern for managing context files (CLAUDE.md, agents, commands) across four permission levels (PUBLIC, ORG, TEAM, PRIVATE) that facilitates contributions while protecting privacy.

## Motivation

Datacore needs to:
1. Enable 1000s of users to contribute improvements back to the system
2. Protect private/sensitive information at multiple levels
3. Apply consistent patterns across all components (repos, modules, agents, commands)
4. Facilitate automatic learning through PRs

Current approach mixes public and private content, making contribution difficult and risking privacy leaks.

## Specification

### Permission Levels

| Level | Suffix | Visibility | Git Tracking | PR Target |
|-------|--------|------------|--------------|-----------|
| PUBLIC | `.base.md` | Everyone | Tracked | Upstream repo |
| ORG | `.org.md` | Organization | Tracked in fork | Org's fork |
| TEAM | `.team.md` | Team members | Optional | Team repo |
| PRIVATE | `.local.md` | Only user | Never | None |

### File Structure

Every configurable component follows this pattern:

```
[component]/
├── [NAME].base.md          # PUBLIC - generic template
├── [NAME].org.md           # ORG - org customizations
├── [NAME].team.md          # TEAM - team additions
├── [NAME].local.md         # PRIVATE - personal notes
└── [NAME].md               # Composed (gitignored)
```

### Composition

Files are merged in order (later overrides/extends earlier):

```
[NAME].base.md      # Layer 1: System defaults
  + [NAME].org.md   # Layer 2: Org customizations
  + [NAME].team.md  # Layer 3: Team additions
  + [NAME].local.md # Layer 4: Personal notes
  ─────────────────
  = [NAME].md       # Output: Complete context
```

The composed `[NAME].md` is:
- Generated automatically (not manually edited)
- Always gitignored
- Read by AI at runtime

### Merge Behavior

Each layer can:
- **Add sections** - New headers are appended
- **Extend sections** - Content under same header is concatenated
- **Override values** - Specific key-value patterns can be overwritten

Merge utility: `.datacore/lib/context_merge.py`

### Applied Components

| Component | Base Location | Example |
|-----------|---------------|---------|
| System context | `datacore/CLAUDE.base.md` | GTD methodology |
| Org template | `datacore-org/CLAUDE.base.md` | Org structure |
| Space context | `space/CLAUDE.base.md` | Space overview |
| Module context | `modules/[name]/CLAUDE.base.md` | Trading rules |
| Agent definition | `agents/[name].base.md` | Agent prompt |
| Command definition | `commands/[name].base.md` | Command prompt |

### gitignore Pattern

Standard `.gitignore` for all components:

```gitignore
# Private layer - never tracked
*.local.md

# Composed output - generated at runtime
CLAUDE.md

# Team layer - optional tracking
# *.team.md  # Uncomment if team uses separate repo

# Tracked (PUBLIC + ORG):
# *.base.md
# *.org.md
```

### Contribution Flow

```
User improves something
        │
        ├─► Generic improvement ──► Edit .base.md ──► PR to upstream
        │
        ├─► Org-specific ──► Edit .org.md ──► Commit to fork
        │
        ├─► Team-specific ──► Edit .team.md ──► Share with team
        │
        └─► Personal ──► Edit .local.md ──► Stays local
```

### Auto-PR Triggers

When `.base.md` or `.org.md` changes:

1. **Pre-commit hook** checks classification
2. **CI workflow** validates no private content in public layers
3. **Optional**: Auto-create draft PR to upstream

### Example: Trading Module

```
.datacore/modules/trading/
├── CLAUDE.base.md              # Generic trading methodology
│   └── "Position sizing rules, risk management..."
├── CLAUDE.org.md               # Org's trading focus
│   └── "We focus on crypto perpetuals..."
├── CLAUDE.team.md              # Team preferences
│   └── "Preferred exchanges: Binance, Bybit..."
├── CLAUDE.local.md             # Personal settings
│   └── "My risk tolerance: 2% per trade..."
└── CLAUDE.md                   # Composed (gitignored)
    └── [All layers merged]

├── agents/
│   ├── position-manager.base.md    # Generic agent
│   ├── position-manager.org.md     # Org risk limits
│   ├── position-manager.local.md   # My thresholds
│   └── position-manager.md         # Composed
```

### Merge Utility

```python
# .datacore/lib/context_merge.py

def merge_context(component_path: str, name: str = "CLAUDE") -> str:
    """Merge layered context files into single output."""
    layers = [
        f"{name}.base.md",   # PUBLIC
        f"{name}.org.md",    # ORG
        f"{name}.team.md",   # TEAM
        f"{name}.local.md",  # PRIVATE
    ]

    content = []
    for layer in layers:
        path = component_path / layer
        if path.exists():
            content.append(f"<!-- Layer: {layer} -->\n")
            content.append(path.read_text())
            content.append("\n")

    return "".join(content)
```

### Commands

```bash
# Regenerate all composed files
datacore context rebuild

# Regenerate specific component
datacore context rebuild --path .datacore/modules/trading

# Validate no private content in public layers
datacore context validate

# Show which layer a section comes from
datacore context trace "Position Sizing"
```

## Rationale

**Why four levels?**
- Maps to common organizational structures (self, team, org, world)
- Matches privacy-policy.md classification (PRIVATE, TEAM, ORG, PUBLIC)
- Provides granular control without complexity

**Why file-based (not section-based)?**
- Easier to enforce gitignore rules
- Clear ownership per file
- No parsing complexity
- Harder to accidentally leak private content

**Why composition (not inheritance)?**
- Additive model is simpler to understand
- All context visible in one file at runtime
- No need to traverse hierarchy

## Backwards Compatibility

Migration from current pattern:

| Current | New |
|---------|-----|
| `CLAUDE.template.md` | `CLAUDE.base.md` |
| `CLAUDE.md` (tracked) | `CLAUDE.org.md` |
| `CLAUDE.md` (local) | `CLAUDE.local.md` |

Migration script provided in implementation.

## Security Considerations

1. **CI validation** - PRs checked for private content patterns
2. **Pre-commit hooks** - Warn if `.local.md` staged
3. **Gitignore enforcement** - `.local.md` always ignored
4. **Content scanning** - Detect PII, secrets, sensitive patterns

### Private Content Patterns (blocked in public layers)

```regex
# Personal identifiers
/\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b/  # Email
/\b\d{3}[-.]?\d{3}[-.]?\d{4}\b/  # Phone

# Secrets
/api[_-]?key|password|secret|token/i

# Financial
/\$[\d,]+\.\d{2}/  # Dollar amounts
/position|P&L|balance/i  # Trading specifics
```

## Implementation

### Phase 1: Core Pattern
- [ ] Create `context_merge.py` utility
- [ ] Update `.gitignore` templates
- [ ] Create migration script

### Phase 2: Apply to Repos
- [ ] Update `datacore` repo
- [ ] Update `datacore-org` template
- [ ] Update `datacore-trading` module

### Phase 3: Tooling
- [ ] Add `datacore context` commands
- [ ] Create pre-commit hooks
- [ ] Add CI validation workflow

### Phase 4: Documentation
- [ ] Update INSTALL.md
- [ ] Update CONTRIBUTING.md
- [ ] Add examples to CATALOG.md

## Open Questions

1. Should `.team.md` be tracked by default or opt-in?
2. How to handle merge conflicts between layers?
3. Should composed files include layer markers for debugging?

## References

- [DIP-0001: Contribution Model](./DIP-0001-contribution-model.md)
- [Privacy Policy](../.datacore/specs/privacy-policy.md)
- [Ethereum EIP Process](https://eips.ethereum.org/EIPS/eip-1)
