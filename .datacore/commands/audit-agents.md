---
name: audit-agents
description: audit-agents command
recall:
  # DIP-0029 default — engrams scoped to this command + tag-matched.
  scopes:
    - command:audit-agents
  tags:
    - audit-agents
---

# /audit-agents

## Command Context

### When to Reference DIP-0016

**Always reference when:**
- Auditing agent registry entries
- Checking spawn relationships
- Validating reads/writes paths
- Injecting Agent Context sections

**Key decisions this DIP informs:**
- Registry entry requirements
- Agent Context section format
- Spawn cycle detection
- Compliance scoring

### Quick Reference

| Question | Answer |
|----------|--------|
| Registry file? | `.datacore/registry/agents.yaml` |
| Commands registry? | `.datacore/registry/commands.yaml` |
| Agent files? | `.datacore/agents/*.md` |
| What DIPs govern this? | DIP-0016 (Agent Registry) |

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| `agent-registry-auditor` | Compliance audit |

### Integration Points

- **DIP-0016** - Agent registry specification
- **/diagnostic** - System health complement

---

Audit agents for DIP-0016 compliance and registry alignment.

## Workflow

### Step 1: Understand Intent

If user invoked `/audit-agents` with no arguments, ask:

"What would you like to audit?"

1. **Full audit** - Scan all agents, check registry, detect issues (Recommended)
2. **Specific agent** - Audit a single agent by name
3. **Generate missing** - Only generate entries for unregistered agents
4. **Fix issues** - Run audit and auto-fix with confirmation

If intent is clear from context (e.g., `/audit-agents ai-task-executor`), proceed directly.

### Step 2: Run Audit

Invoke the `agent-registry-auditor` agent with the selected scope:

```
Launching agent-registry-auditor...
```

The auditor will:
1. Scan all agent files in `.datacore/agents/` and module agent directories
2. Compare against `.datacore/registry/agents.yaml`
3. Validate spawn relationships and detect cycles
4. Check that read paths exist
5. Generate compliance report

### Step 3: Present Results

Show the compliance report:

```
AGENT REGISTRY AUDIT REPORT
═══════════════════════════════════════════════════════════════

Summary:
  Total agents:        25
  Fully compliant:     22
  Needs attention:      3

Issues:
  [!] agent-name - Missing registry entry
  [!] other-agent - Spawns non-existent target
  ...

═══════════════════════════════════════════════════════════════
```

### Step 4: Offer Actions

After presenting results, offer:

"What would you like to do?"

1. **Generate entries** - Create registry entries for missing agents
2. **Fix issues** - Auto-fix detected problems
3. **Upgrade agent** - Add Agent Context section to specific agent
4. **View details** - Show detailed info for specific agent
5. **Done** - Exit audit

## Arguments

| Argument | Description | Example |
|----------|-------------|---------|
| (none) | Interactive mode - shows menu | `/audit-agents` |
| `<agent-name>` | Audit specific agent | `/audit-agents ai-task-executor` |
| `--dry-run` | Show what would change, don't modify | `/audit-agents --dry-run` |
| `--fix` | Auto-fix all issues with confirmation | `/audit-agents --fix` |
| `--generate` | Only generate missing entries | `/audit-agents --generate` |

## Examples

```bash
# Interactive full audit
/audit-agents

# Audit specific agent
/audit-agents gtd-inbox-processor

# See what would change without modifying
/audit-agents --dry-run

# Auto-fix all issues (with confirmation)
/audit-agents --fix

# Only generate missing registry entries
/audit-agents --generate
```

## Settings Reference

User can configure in `~/.datacore/settings.local.yaml`:

```yaml
audit:
  auto_fix: false           # Skip confirmation for fixes
  skip_deprecated: true     # Don't report deprecated agents
  include_modules: true     # Include module agents in audit
```

## Error Handling

**Registry file not found:**
```
Registry not found at .datacore/registry/agents.yaml

Solution:
  Run: /audit-agents --generate
  This will create the registry with entries for all agents.
```

**Agent file not found:**
```
Agent file not found: .datacore/agents/missing-agent.md

The registry references an agent that doesn't exist.

Options:
1. Remove entry from registry
2. Create the agent file
3. Update source path in registry
```

**Circular spawn detected:**
```
CIRCULAR SPAWN DETECTED

Chain: agent-a → agent-b → agent-c → agent-a

This creates an infinite loop potential.

Solution:
  Review the spawn relationships and break the cycle.
  Usually one agent should use can_be_called_by instead.
```

## Your Boundaries

**YOU CAN:**
- Invoke agent-registry-auditor
- Parse audit results
- Offer fix options
- Apply fixes with confirmation

**YOU CANNOT:**
- Delete agent files
- Modify agent behavior
- Apply fixes without showing what will change
- Skip validation steps

**YOU MUST:**
- Show audit results before offering fixes
- Confirm before any modifications
- Report deprecated agents
- Check for circular dependencies
