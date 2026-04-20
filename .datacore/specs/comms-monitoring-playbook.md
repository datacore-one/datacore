# Campaign Health Monitoring Playbook

**Version:** 1.0
**Last Updated:** 2026-04-20
**Owner:** AI Task Executor / Comms Module

## Purpose

This playbook prevents the "forgotten pipeline" pattern that caused multiple campaign gaps:
- **February 2026**: 16-day silence (Feb 1-16)
- **March-April 2026**: 32-day silence (Mar 17 - Apr 18)

Campaign health monitoring automatically detects when scheduled posts stop publishing and alerts before multi-week gaps occur.

## Problem Statement

**Root Cause:** Late.dev API credentials expire or become invalid, causing scheduled posts to fail silently without human-visible errors. The Late.dev dashboard shows "scheduled" posts, but they never publish.

**Impact:** Campaigns go dark for weeks, losing momentum, audience engagement, and strategic timing.

**Solution:** Daily automated health checks verify actual publishing activity (not just scheduled posts) and alert when 48+ hours pass without a published post.

## Architecture

### Components

1. **Health Check Script** (`check-campaign-health.py`)
   - Polls Late API for recently published posts
   - Checks if at least 1 post published in past 48 hours
   - Alerts to journal + stdout if silent
   - Tracks health history for trend analysis

2. **Dashboard** (`dashboard.py`)
   - Formats health status for /today briefings
   - Shows last post time, scheduled posts, alert history
   - Provides visual health indicators (✅ ⚠️ 🚨)

3. **Scheduler** (cron or systemd timer)
   - Runs health check daily at 09:00 UTC
   - Ensures monitoring happens even when user is offline

4. **State Storage** (`.datacore/state/comms/campaign-health-status.json`)
   - Persists check history, alert timestamps
   - Tracks consecutive silent days for escalation

5. **Logging** (Space journals + `.datacore/logs/`)
   - Journal entries for human review during /today
   - Persistent logs for debugging

## Installation

### Step 1: Verify Prerequisites

```bash
# Check Late API key is configured
cat ~/.datacore/env/.env | grep LATE_API_KEY

# Install dependencies (if needed)
pip install requests
```

### Step 2: Run Setup Script

```bash
cd ~/Data

# Setup with cron (recommended for desktop/laptop)
bash .datacore/modules/comms/monitoring/setup-monitoring.sh cron "09:00"

# OR setup with systemd (recommended for servers)
bash .datacore/modules/comms/monitoring/setup-monitoring.sh systemd "09:00"
```

### Step 3: Manual Test

```bash
# Dry run to verify configuration
python3 .datacore/modules/comms/monitoring/check-campaign-health.py --dry-run

# View dashboard
python3 .datacore/modules/comms/monitoring/dashboard.py
```

### Step 4: Verify Scheduling

**For cron:**
```bash
crontab -l | grep campaign-health
```

**For systemd:**
```bash
sudo systemctl status campaign-health.timer
sudo systemctl list-timers --all | grep campaign
```

## Daily Operations

### Morning Briefing (/today)

The dashboard is automatically included in `/today` briefings via the comms module hook:

```bash
/today
```

Expected output section:
```
### Campaign Health

✅ **HEALTHY**

- Last post: ✅ 18.3h ago
- Last check: 2.1h ago
```

### Alert Workflow

When a silence alert is triggered:

1. **Alert Location:** Check today's journal in the relevant space:
   - Personal: `~/Data/0-personal/journal/YYYY-MM-DD.md`
   - Datafund: `~/Data/1-datafund/journal/YYYY-MM-DD.md`
   - FDS: `~/Data/3-fds/journal/YYYY-MM-DD.md`

2. **Alert Format:**
   ```
   🚨 **09:15 UTC - Campaign Health (CRITICAL):** Campaign has been SILENT for 2 days, 6 hours (threshold: 48h). Last post: 2026-04-18 12:34 UTC. Scheduled posts: 12. This is the pattern that caused the 32-day gap (Mar 17 - Apr 18). ACTION REQUIRED: Check Late dashboard and verify posting pipeline.
   ```

3. **Immediate Actions:**
   - Visit Late.dev dashboard (https://app.getlate.dev)
   - Check "Published" tab - verify posts are actually publishing
   - Check "Scheduled" tab - verify posts exist and have future times
   - Check "Settings > Accounts" - verify API connections are active

4. **Root Cause Diagnosis:**

   **Symptom:** Scheduled posts exist but aren't publishing
   - **Cause:** Late API credentials expired or invalid
   - **Fix:** Reconnect Twitter/X account in Late dashboard
   - **Verification:** Check "Accounts" page for red warning icons

   **Symptom:** No scheduled posts in queue
   - **Cause:** Scheduling pipeline broke (script error, workflow gap)
   - **Fix:** Run scheduling script manually or create posts via Late dashboard
   - **Verification:** See posts in "Scheduled" tab

   **Symptom:** API returns error in health check logs
   - **Cause:** LATE_API_KEY expired or revoked
   - **Fix:** Generate new API key in Late dashboard > Settings > API
   - **Update:** Add to `.datacore/env/.env`

5. **Post-Fix Verification:**
   ```bash
   # Manually trigger health check
   python3 .datacore/modules/comms/monitoring/check-campaign-health.py

   # Should show: ✅ Campaign is healthy
   ```

### Weekly Review

During weekly GTD review, check campaign health trends:

```bash
# View last 7 days of health history
cat .datacore/state/comms/campaign-health-status.json | jq '.health_history[-7:]'
```

Look for:
- Consecutive silent days > 0 → Investigate recurring issue
- API errors > 1/week → Check credential stability
- Scheduled post count trending down → Verify content pipeline

## Monitoring Thresholds

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| Hours since last post | < 48h | 48-72h | > 72h |
| Consecutive silent days | 0 | 1-2 | > 2 |
| Scheduled posts in queue | > 5 | 1-5 | 0 |
| API health | 200 OK | Intermittent errors | 401/403 |

## Credential Management

### Late API Key

**Location:** `.datacore/env/.env`

**Format:**
```
LATE_API_KEY=late_abc123...
```

**Rotation Schedule:**
- Late API keys don't expire automatically
- Rotate every 6 months as security best practice
- Rotate immediately if compromised

**Expiration Warning:**
Currently, Late.dev doesn't provide key expiration dates via API. Monitor for:
- Sudden 401/403 errors
- "Invalid token" messages in logs

### Account Connection Health

Late dashboard shows account connection status. Monitor for:
- Red warning icons on "Accounts" page
- "Reconnect required" messages
- Posts stuck in "pending" state for >24h

**Prevention:** Set calendar reminder to check Late dashboard connections monthly (1st of each month).

## Troubleshooting

### Health Check Not Running

**Symptom:** `last_check` timestamp in status file is >25 hours old

**Diagnosis:**
```bash
# Check cron logs
grep -i campaign /var/log/syslog

# Check systemd timer status
sudo systemctl status campaign-health.timer
sudo journalctl -u campaign-health.service -n 50
```

**Common Causes:**
- Cron daemon not running: `sudo systemctl status cron`
- Script path changed: Update crontab
- Python environment issue: Check shebang in script

### False Positives

**Symptom:** Alert triggered but posts are actually publishing

**Causes:**
- Late API returning stale data
- Timezone confusion (script uses UTC)
- Recent post not yet reflected in API

**Mitigation:**
```bash
# Check Twitter/X directly
xdg-open "https://x.com/FairDataSociety"

# Manually verify via Late dashboard
xdg-open "https://app.getlate.dev"

# Force refresh check
python3 .datacore/modules/comms/monitoring/check-campaign-health.py
```

### Alert Spam

**Symptom:** Receiving duplicate alerts within 24 hours

**Protection:** Built-in rate limiting prevents alerts more than once per 24 hours. If still seeing duplicates:

```bash
# Check last alert time
cat .datacore/state/comms/campaign-health-status.json | jq '.last_alert_time'

# If stuck, reset status
rm .datacore/state/comms/campaign-health-status.json
```

## Integration with Other Systems

### /today Hook

The dashboard is called by the comms module's `/today` hook. Configuration:

**Hook location:** `.datacore/modules/comms/hooks/today/campaign-health.sh`

```bash
#!/bin/bash
python3 .datacore/modules/comms/monitoring/dashboard.py --format markdown
```

### Telegram Bot (Future)

Integration point for mobile alerts:

```python
# In telegram bot handler
from comms.monitoring.dashboard import load_status, get_health_status

status = load_status()
health = get_health_status(status)

if health in ["warning", "critical"]:
    send_telegram_alert(f"Campaign Health: {health}")
```

### Nightshift (Future)

Auto-remediation for common issues:

```yaml
# In nightshift workflow
- name: campaign-health-auto-fix
  trigger: campaign_health_alert
  actions:
    - check_late_credentials
    - regenerate_api_key_if_expired
    - reconnect_accounts_if_needed
    - notify_human_if_unfixable
```

## Metrics & KPIs

### Success Criteria

- **Zero gaps >48h undetected** - Primary goal
- **Alert-to-fix time <4h** - Secondary goal
- **False positive rate <5%** - Quality goal

### Tracking

```bash
# Calculate metrics from history
python3 << 'EOF'
import json
from pathlib import Path
from datetime import datetime

status_file = Path("~/.datacore/state/comms/campaign-health-status.json").expanduser()
status = json.loads(status_file.read_text())

history = status.get("health_history", [])
total_checks = len(history)
silence_detected = sum(1 for h in history if h.get("silence_detected"))

print(f"Total checks: {total_checks}")
print(f"Silence detections: {silence_detected}")
print(f"Detection rate: {silence_detected/total_checks*100:.1f}%")
EOF
```

## Runbook Quick Reference

| Situation | Command | Expected Result |
|-----------|---------|-----------------|
| Check current health | `python3 .datacore/modules/comms/monitoring/dashboard.py` | Status summary |
| Force health check | `python3 .datacore/modules/comms/monitoring/check-campaign-health.py` | Alert if silent |
| Test without alerts | `python3 .datacore/modules/comms/monitoring/check-campaign-health.py --dry-run` | Dry run output |
| View raw status | `cat .datacore/state/comms/campaign-health-status.json \| jq` | JSON state |
| Check scheduler | `crontab -l \| grep campaign` | Cron entry |
| View logs | `tail -f .datacore/logs/campaign-health.log` | Live logs |
| Reset state | `rm .datacore/state/comms/campaign-health-status.json` | Clean slate |

## Change Log

| Date | Version | Changes |
|------|---------|---------|
| 2026-04-20 | 1.0 | Initial implementation - health check, dashboard, scheduling, documentation |

## Future Enhancements

1. **Multi-Account Support**
   - Monitor multiple Late accounts
   - Per-account thresholds
   - Consolidated health dashboard

2. **Credential Expiration Tracking**
   - 30-day warning before API key rotation due
   - Auto-check account connection status
   - Proactive credential refresh

3. **Auto-Remediation**
   - Automatic API key regeneration
   - Account reconnection via Late API
   - Self-healing pipeline

4. **Advanced Analytics**
   - Post frequency trends
   - Engagement correlation with posting cadence
   - Optimal posting time recommendations

5. **Mobile Alerts**
   - Telegram notifications for critical alerts
   - Push notifications via Pushover/ntfy
   - SMS for 72+ hour silence

6. **Integration with Engagement Monitoring**
   - Unified comms health dashboard
   - Cross-reference posting activity with engagement metrics
   - Detect "posting but zero engagement" scenarios

---

**Document Owner:** AI Task Executor
**Review Cadence:** Monthly
**Next Review:** 2026-05-20
