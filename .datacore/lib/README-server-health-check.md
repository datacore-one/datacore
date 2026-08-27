# Server Health Check System

Comprehensive weekly health monitoring for Datacore installation.

## Overview

The `server_health_check.py` script generates detailed markdown reports covering:

- **Disk usage** - Overall and Data directory usage with alerts
- **Git status** - All repositories with uncommitted/unpushed changes
- **Running services** - Process list with CPU/memory usage
- **Process start times** - Key service uptime tracking
- **Service health checks** - Expected vs running services comparison

## Usage

### Manual Execution

```bash
# Generate health check report
python3 ~/Data/.datacore/lib/server_health_check.py

# Report will be saved to:
# ~/Data/0-personal/0-inbox/server-health-check-YYYY-MM-DD.md
```

### Automated Weekly Execution (via Nightshift)

Add to `next_actions.org`:

```org
*** TODO Weekly server health check                    :AI:technical:
SCHEDULED: <2026-03-11 Wed +1w>
:PROPERTIES:
:CREATED: [2026-03-04 Wed 18:56]
:EFFORT: 0:05
:PRIORITY: C
:CATEGORY: Infrastructure
:END:

Run the weekly server health check script and review the report.

Script: ~/Data/.datacore/lib/server_health_check.py
Output: 0-personal/0-inbox/server-health-check-{date}.md
```

## Report Sections

### Executive Summary
- Quick stats on repositories, services, disk usage
- Alert indicators for issues requiring attention

### Disk Usage
- Root partition and Data directory usage
- Percentage used with alert thresholds (80% warning)

### Git Repository Status
- All repositories with their current branch
- Uncommitted changes count
- Unpushed commits count
- Last commit information

### Service Health Check
- Expected services vs running services
- Status indicators (OK/MISSING/OPTIONAL_MISSING)
- Required vs optional service classification

### Running Services (Detail)
- PID, CPU%, Memory% for each service
- Full command line for verification

### Process Start Times
- When key services were started
- Useful for uptime tracking and restart detection

### System Information
- System uptime
- Kernel version
- Report generation timestamp

### Recommendations
- Actionable items based on detected issues
- Prioritized by severity

## Monitored Services

### Required Services
- **Nightshift** - AI task executor for overnight processing
  - Pattern: `nightshift`
  - Multiple processes expected (watchdog, runner, worker)

### Optional Services
- **Telegram Bot** - CRM and notification integration
  - Pattern: `telegram.*bot`
  - Single process expected

## Alert Thresholds

- **Disk Usage**: Warning at >80%
- **Uncommitted Changes**: Warning if any repositories have changes
- **Unpushed Commits**: Warning if any repositories are out of sync
- **Missing Required Services**: Error if critical services not running

## Output Location

Reports are saved to: `~/Data/0-personal/0-inbox/`

Filename format: `server-health-check-YYYY-MM-DD.md`

This ensures reports are:
1. In the GTD inbox for human review
2. Date-stamped for weekly comparison
3. In markdown for easy reading/archival

## Adding New Services

To monitor additional services, edit the `get_expected_services()` function:

```python
def get_expected_services():
    return [
        {
            "name": "Service Name",
            "pattern": "regex_pattern",  # Used in ps aux | grep
            "required": True,  # or False for optional
            "description": "Human-readable description"
        },
        # ... more services
    ]
```

## Customization

### Change Output Directory

Edit line in `generate_markdown_report()`:

```python
output_path = f"/your/custom/path/server-health-check-{timestamp}.md"
```

### Add Custom Metrics

Add new data collection functions and include in the report generation.

## Integration Points

- **Nightshift Module**: Can be triggered as :AI:technical: task
- **GTD Inbox**: Reports land in 0-inbox for daily review
- **Git Repos**: Monitors all repositories in ~/Data
- **System Processes**: Uses `ps`, `systemctl` for service detection

## Troubleshooting

### Script Not Finding Services

Check the process patterns in `get_expected_services()` - patterns are matched against `ps aux` output.

### Disk Usage Shows Same for / and Data

This is normal if ~/Data is on the same partition as root.

### Git Repos Not Detected

Script looks for directories containing `.git/` in `~/Data/`.

Ensure repositories have `.git` directory (not bare repos).

## Future Enhancements

Potential additions:
- Network connectivity checks
- Database health (if applicable)
- API endpoint health checks
- Log file size monitoring
- Backup status verification
- Certificate expiration warnings

---

**Created**: 2026-03-04
**Last Updated**: 2026-03-04
**Maintainer**: AI Task Executor (nightshift)
