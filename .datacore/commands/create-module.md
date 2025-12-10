# /create-module

Create, convert, or audit Datacore modules for spec alignment.

## Workflow

### Step 1: Understand Intent

If user invoked `/create-module` with no clear intent, ask:

"What would you like to do?"

1. **Create new** - Start a new module from scratch with correct structure
2. **Convert existing** - Turn existing code into a spec-aligned module
3. **Audit** - Check an existing module for spec alignment and suggest improvements

If intent is clear from context (e.g., "make datacortex a module"), proceed directly.

### Step 2: Gather Information

**For new module:**
- Ask: "What should the module be called?" (lowercase, hyphenated)
- Ask: "What does it do?" (one-line description)
- Ask: "What will it provide?" (commands, agents, templates)
- Default location: `.datacore/modules/<name>`

**For conversion:**
- Ask: "Where is the code?" (path to existing project)
- Then same questions as new module

**For audit:**
- Ask: "Which module?" (path or name)

### Step 3: Execute

Invoke the `create-module` agent with gathered information.

The agent will:
1. Scaffold/verify structure (module.yaml, CLAUDE.base.md, .gitignore)
2. Create/improve commands as conversational agents
3. Run spec alignment audit
4. Suggest UX improvements
5. Optionally register in CATALOG

### Step 4: Follow-up

After completion, ask:
- "Would you like to register this module in CATALOG?"
- "Want to create another module?"
- "Shall I audit your other modules?"

## Examples

```bash
# Interactive mode - asks what you want
/create-module

# Create new module directly
/create-module new my-analytics

# Convert existing code
/create-module convert ~/Data/1-datafund/2-projects/datacortex

# Audit existing module
/create-module audit .datacore/modules/trading
```

## What It Does

| Action | Result |
|--------|--------|
| **Create new** | Scaffolds module.yaml, CLAUDE.base.md, commands/, .gitignore with correct structure |
| **Convert** | Adds module structure to existing code, suggests conversational command rewrites |
| **Audit** | Checks spec alignment, suggests missing settings/use_cases/hooks, improves UX |

## Spec Alignment Checklist

The agent validates:

**Required:**
- module.yaml with name, version, description, provides
- CLAUDE.base.md (layered context)
- .gitignore for layered files

**Recommended:**
- settings section in module.yaml
- use_cases list for discoverability
- hooks.post_install for friendly messages
- Conversational commands (not CLI wrappers)
- Boundaries section in commands
- Error handling with solutions

## Error Handling

**Module already exists:**
```
Module already exists at <path>.

Options:
1. Audit existing module for improvements
2. Overwrite (will backup existing)
3. Cancel
```

**Invalid module name:**
```
Module name should be lowercase with hyphens.

Examples: my-module, data-analytics, graph-viz
```

## Your Boundaries

**YOU CAN:**
- Create module scaffolding
- Suggest UX improvements
- Rewrite CLI wrappers as conversational commands
- Delegate to module-registrar for CATALOG

**YOU CANNOT:**
- Delete existing code without asking
- Register without confirmation
- Skip the audit step

**YOU MUST:**
- Ask before overwriting files
- Explain why each suggestion improves UX
- Follow spec patterns exactly
