# Datacore Privacy Policy & Data Classification

**Version**: 1.1
**Created**: 2025-11-29
**Updated**: 2025-12-01
**Status**: Implemented

## Related DIPs

| DIP | Relationship |
|-----|--------------|
| [DIP-0001](../../dips/DIP-0001-contribution-model.md) | Defines contribution model using this policy |
| [DIP-0002](../../dips/DIP-0002-layered-context-pattern.md) | Implements four-level privacy via file layers |

## Purpose

This document defines what data is shareable (to repo, to agents, externally) and what must remain private. It serves as guidelines for both human users and AI agents.

## Data Classification Levels

### Level 1: PUBLIC (can be in repo, shared anywhere)
Data that can be committed to public GitHub repo and shared freely.

### Level 2: TEAM (shareable within team spaces)
Data that can be shared within a team context but not publicly.

### Level 3: PRIVATE (never leaves local machine)
Personal data that must never be committed or shared externally.

---

## File-Level Classification

### PUBLIC Files (Track in Repo)

| Location | Content Type | Notes |
|----------|--------------|-------|
| `.datacore/agents/*.md` | Agent definitions | Generic methodology |
| `.datacore/commands/*.md` | Command definitions | Generic workflows |
| `.datacore/lib/*.py` | Library code | No hardcoded paths |
| `.datacore/specs/*.md` | Specifications | System documentation |
| `0-datacore/` | Public datacore space | Shareable knowledge |
| `sync` | Sync script | Utility |

### PRIVATE Files (Never Track)

| Location | Content Type | Risk |
|----------|--------------|------|
| `**/*.org` | GTD tasks | Tasks, priorities, personal commitments |
| `**/journals/*.md` | Daily journals | Personal reflections, decisions |
| `**/pages/*.md` | Wiki pages | Personal knowledge, notes |
| `**/Clippings/*.md` | Web clippings | Reading habits, interests |
| `**/*.db` | SQLite databases | Indexed personal content |
| `**/2-knowledge/zettel/` | Personal zettels | Synthesized personal insights |
| `**/2-knowledge/literature/` | Literature notes | Reading notes |
| `install.yaml` | Installation config | Names, repo references |
| `CLAUDE.md` (root) | Installation instructions | Personal space names |
| `0-personal/CLAUDE.md` | Personal space docs | Focus areas, projects |
| `.datacore/settings.local.json` | Local settings | Permissions |
| `.datacore/comms/` | Communications | Strategy, positioning |
| `**/content/` | Generated content | Personal outputs |

### CONDITIONAL Files (Depends on Location)

| Pattern | When PUBLIC | When PRIVATE |
|---------|-------------|--------------|
| `CLAUDE.md` | In `.datacore/modules/` | In spaces (`0-personal/`, `1-*/`) |
| `README.md` | In `.datacore/`, `0-datacore/` | In personal folders |
| `*.md` in `2-knowledge/` | In `0-datacore/` (curated) | In `0-personal/` |

---

## Database Privacy

### Knowledge Database (`.datacore/knowledge.db`)

**Contains:**
- File index (titles, paths, content hashes)
- Links between notes (source → target)
- Tags and topics
- Full-text search index

**Risk Level:** PRIVATE - reveals personal knowledge structure

**Policy:**
- Never commit `.db` files
- Database is regenerated locally via `zettel_processor.py`
- Schema can be public, data cannot

### What Database Reveals

Even without content, the database reveals:
- What topics you're interested in
- How you connect ideas
- What you're reading/researching
- Project names and structure

---

## Agent Data Handling

### What Agents CAN Access

- All local files (needed for task execution)
- Database queries (needed for knowledge work)
- External URLs (for research)

### What Agents MUST NOT Share

When generating output or communicating:

1. **Personal identifiers**
   - Full name, email, phone
   - Home address, location
   - Usernames, account names

2. **Financial information**
   - Trading positions, P&L
   - Account balances
   - Investment strategies (specifics)

3. **Health information**
   - Medical conditions
   - Health metrics
   - Treatment details

4. **Relationship information**
   - Family member names
   - Personal relationships
   - Private communications

5. **Business confidential**
   - Unreleased product details
   - Internal strategy documents
   - Team member information

### Agent Output Guidelines

When creating shareable content:
- Generalize personal examples
- Remove specific names/dates
- Abstract strategies to principles
- Check for embedded personal references

---

## Repo Structure Guidelines

### Pattern: Template + Local Override

For files that need both public template and private customization:

```
.datacore/
├── CLAUDE.md.template     # PUBLIC - generic template
├── CLAUDE.md              # PRIVATE (gitignored) - local version
```

**Applies to:**
- `CLAUDE.md` (root and spaces)
- `install.yaml` → `install.yaml.example`
- Any config with personal data

### Pattern: Public Scaffold, Private Content

Directories where structure is public but content is private:

```
0-personal/notes/
├── README.md              # PUBLIC - explains structure
├── journals/              # PRIVATE - actual journals
│   └── .gitkeep          # PUBLIC - preserves folder
```

---

## .gitignore Requirements

Based on this policy, gitignore MUST exclude:

```gitignore
# Configuration with personal data
install.yaml
CLAUDE.md
*/CLAUDE.md

# All org-mode files
**/*.org
**/*.org~
**/*.org_archive

# All journals and personal notes
**/journals/
**/pages/
**/Clippings/
**/2-knowledge/zettel/
**/2-knowledge/literature/

# Databases
**/*.db

# Local settings
.datacore/settings.local.json
.datacore/comms/

# Content outputs
**/content/

# Team spaces (separate repos)
/[1-9]-*/
```

---

## Sharing Decision Tree

```
Is this data going to repo?
├── Does it contain personal identifiers? → EXCLUDE
├── Does it contain task/project details? → EXCLUDE
├── Does it reference team members by name? → EXCLUDE
├── Is it methodology/code that works for anyone? → INCLUDE
└── Is it curated public knowledge (0-datacore)? → INCLUDE

Is an agent generating output?
├── Is it for local use only? → Full context OK
├── Is it for team space? → Remove personal details
└── Is it for public sharing? → Generalize completely
```

---

## Implementation Checklist

- [ ] Update `.gitignore` per requirements
- [ ] Create `.template` versions of config files
- [ ] Remove tracked files that should be private
- [ ] Add `.gitkeep` to preserve empty directories
- [ ] Document in root `CLAUDE.md` reference to this policy
- [ ] Add agent instructions referencing this policy

---

## Exceptions Process

To add a private file to repo:
1. Review against classification criteria
2. Create sanitized version if needed
3. Document exception in this file
4. Get explicit approval before commit

**Current Exceptions:**
- None

---

*This policy is enforced by gitignore patterns and agent instructions. Violations should be caught in PR review.*
