# Diagnostic

**"All hands, this is the bridge. Prepare for full systems diagnostic."**

Run a comprehensive diagnostic of all Datacore systems to verify installation health and operational status.

## Behavior

Execute a Level 1 diagnostic sequence, checking all primary and secondary systems. Report status using TNG-style terminology.

## Diagnostic Sequence

### 1. Primary Systems Check

**Core Matrix:**
- Verify `~/Data` exists and is accessible
- Check `.datacore/` directory structure
- Verify CLAUDE.md present at root

**Report format:**
```
PRIMARY SYSTEMS
---------------
Core Matrix.............. [OPERATIONAL/OFFLINE]
  - Root directory: [path]
  - .datacore config: [PRESENT/MISSING]
  - CLAUDE.md: [PRESENT/MISSING]
```

### 2. Command Processors

**Check each command file in `.datacore/commands/`:**
- today.md
- gtd-daily-start.md
- gtd-daily-end.md
- gtd-weekly-review.md
- gtd-monthly-strategic.md
- diagnostic.md (this file)

**Report format:**
```
COMMAND PROCESSORS
------------------
/today................... [ONLINE/OFFLINE]
/gtd-daily-start......... [ONLINE/OFFLINE]
/gtd-daily-end........... [ONLINE/OFFLINE]
/gtd-weekly-review....... [ONLINE/OFFLINE]
/gtd-monthly-strategic... [ONLINE/OFFLINE]
/diagnostic.............. [ONLINE]
```

### 3. Agent Subsystems

**Check each agent file in `.datacore/agents/`:**
- ai-task-executor.md
- gtd-inbox-processor.md
- gtd-content-writer.md
- gtd-data-analyzer.md
- gtd-project-manager.md
- gtd-research-processor.md
- conversation-processor.md
- research-link-processor.md

**Report format:**
```
AGENT SUBSYSTEMS
----------------
ai-task-executor......... [STANDING BY/OFFLINE]
gtd-inbox-processor...... [STANDING BY/OFFLINE]
gtd-content-writer....... [STANDING BY/OFFLINE]
gtd-data-analyzer........ [STANDING BY/OFFLINE]
gtd-project-manager...... [STANDING BY/OFFLINE]
gtd-research-processor... [STANDING BY/OFFLINE]
conversation-processor... [STANDING BY/OFFLINE]
research-link-processor.. [STANDING BY/OFFLINE]
```

### 4. Personal Space Integrity

**Check 0-personal/ structure:**
- org/ directory with inbox.org, next_actions.org, someday.org, habits.org
- notes/ directory with journals/, pages/, 0-inbox/, 1-active/, 2-knowledge/, 3-archive/
- code/ directory
- content/ directory
- CLAUDE.md

**Report format:**
```
PERSONAL SPACE (0-personal/)
----------------------------
GTD Core (org/).......... [OPERATIONAL/DEGRADED/OFFLINE]
  - inbox.org: [PRESENT/MISSING]
  - next_actions.org: [PRESENT/MISSING]
  - someday.org: [PRESENT/MISSING]
  - habits.org: [PRESENT/MISSING]

Knowledge Base (notes/).. [OPERATIONAL/DEGRADED/OFFLINE]
  - journals/: [PRESENT/MISSING]
  - pages/: [PRESENT/MISSING]
  - 0-inbox/: [PRESENT/MISSING]
  - 1-active/: [PRESENT/MISSING]
  - 2-knowledge/: [PRESENT/MISSING]
  - 3-archive/: [PRESENT/MISSING]

Project Bay (code/)...... [OPERATIONAL/OFFLINE]
Content Array (content/). [OPERATIONAL/OFFLINE]
Space CLAUDE.md.......... [PRESENT/MISSING]
```

### 5. Module Status

**Check .datacore/modules/ for installed modules:**

**Report format:**
```
AUXILIARY MODULES
-----------------
Installed modules: [count]
  - trading: [ONLINE/OFFLINE] (if present)
  - [other modules...]

Module bay: [READY/EMPTY]
```

### 6. Support Systems

**Check auxiliary files:**
- install.yaml
- sync script (executable)
- .gitignore

**Report format:**
```
SUPPORT SYSTEMS
---------------
System Manifest (install.yaml)... [PRESENT/MISSING]
Sync Protocol.................... [READY/OFFLINE]
Security Filters (.gitignore).... [ACTIVE/MISSING]
```

### 7. Final Assessment

**Summarize overall status:**

```
===============================
DIAGNOSTIC COMPLETE
===============================

Overall Status: [ALL SYSTEMS OPERATIONAL / MINOR ANOMALIES DETECTED / CRITICAL FAILURES]

[If issues found:]
Recommended Actions:
- [Action 1]
- [Action 2]

[If all clear:]
"All stations report ready, Captain. The ship is yours."
```

## Status Definitions

| Status | Meaning |
|--------|---------|
| OPERATIONAL | Fully functional |
| ONLINE | Available and ready |
| STANDING BY | Agent ready for activation |
| READY | System prepared |
| ACTIVE | Currently engaged |
| PRESENT | File/directory exists |
| DEGRADED | Partially functional, some issues |
| OFFLINE | Not available |
| MISSING | Required component not found |

## Usage

Run this command:
- After initial installation
- When something seems wrong
- After significant system changes
- Periodically to verify health

## Output

Display the full diagnostic report in the terminal. Use monospace formatting for alignment.

If critical issues are found, provide specific remediation steps.

---

*"Diagnostics complete. Awaiting your orders."*
