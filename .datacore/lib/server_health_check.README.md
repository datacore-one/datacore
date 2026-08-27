# Server Health Check Script

## Overview

Weekly health check script that monitors system health and generates detailed markdown reports.

## Location

- **Script:** `~/Data/.datacore/lib/server_health_check.sh`
- **Output:** `~/Data/0-inbox/server-health-YYYY-MM-DD.md`

## What It Checks

1. **Disk Usage**
   - Root filesystem usage
   - Key directory sizes (Data, spaces, .datacore)
   - Alert if usage > 80%

2. **Git Repository Status**
   - Main Data repository
   - All space repositories (0-personal, 1-datafund, 2-datacore, etc.)
   - Uncommitted changes
   - Branch status
   - Ahead/behind remote tracking

3. **Running Services**
   - Nightshift (overnight task execution)
   - Telegram bot
   - All datacore-related processes

4. **Process Start Times**
   - Key service uptime
   - Process IDs
   - Start timestamps

5. **System Information**
   - System uptime
   - Memory usage
   - Load average

## Usage

### Manual Execution

```bash
~/Data/.datacore/lib/server_health_check.sh
```

### Nightshift Integration

Add to nightshift weekly tasks in `~/Data/0-personal/org/nightshift.org`:

```org-mode
*** TODO Weekly server health check                          :AI:technical:
SCHEDULED: <2026-03-12 Thu +1w>
:PROPERTIES:
:EFFORT: 0:05
:CATEGORY: System
:END:

Run weekly health check and review report.

Execute: ~/Data/.datacore/lib/server_health_check.sh
```

Or add as a recurring cron-style task:

```yaml
# In nightshift config
weekly_tasks:
  - name: "Server Health Check"
    schedule: "weekly:wednesday:01:00"
    command: "~/Data/.datacore/lib/server_health_check.sh"
    output_to_inbox: true
```

## Output Format

Generates a comprehensive markdown report with:
- Summary statistics
- Detailed tables and code blocks
- Warning alerts for disk usage
- Git status for all repositories
- Process listings with uptime

## Key Alerts

The script automatically flags:
- Disk usage > 80% (WARNING)
- Repositories with uncommitted changes
- Missing expected services
- Repositories behind/ahead of remote

## Maintenance

The script is self-contained and requires no external dependencies beyond standard Unix tools:
- `df`, `du` - disk usage
- `git` - repository status
- `ps` - process information
- `uptime`, `free` - system info

## Example Output

See `~/Data/0-inbox/server-health-2026-03-05.md` for reference.
