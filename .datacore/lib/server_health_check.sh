#!/bin/bash
# Weekly Server Health Check for Datacore
# Generates a markdown report of system health

DATACORE_ROOT="${DATACORE_ROOT:-$HOME/Data}"
REPORT_DATE=$(date +%Y-%m-%d)
REPORT_FILE="${DATACORE_ROOT}/0-inbox/server-health-${REPORT_DATE}.md"

# Create report header
cat > "$REPORT_FILE" << 'EOF'
# Server Health Check Report

**Generated:** $(date '+%Y-%m-%d %H:%M:%S')
**Host:** $(hostname)

---

EOF

# Use actual date/time in the header
sed -i "s/\$(date '+%Y-%m-%d %H:%M:%S')/$(date '+%Y-%m-%d %H:%M:%S')/" "$REPORT_FILE"
sed -i "s/\$(hostname)/$(hostname)/" "$REPORT_FILE"

# Section 1: Disk Usage
cat >> "$REPORT_FILE" << 'EOF'
## 1. Disk Usage

### Root Filesystem
```
EOF

df -h / | tail -n +2 >> "$REPORT_FILE"

cat >> "$REPORT_FILE" << 'EOF'
```

### Key Directories
| Directory | Size | Usage |
|-----------|------|-------|
EOF

# Check sizes of key directories
for dir in "$DATACORE_ROOT" \
           "$DATACORE_ROOT/0-personal" \
           "$DATACORE_ROOT/1-datafund" \
           "$DATACORE_ROOT/2-datacore" \
           "$DATACORE_ROOT/3-fds" \
           "$DATACORE_ROOT/4-forge" \
           "$DATACORE_ROOT/.datacore"; do
    if [ -d "$dir" ]; then
        size=$(du -sh "$dir" 2>/dev/null | cut -f1)
        echo "| $dir | $size | |" >> "$REPORT_FILE"
    fi
done

cat >> "$REPORT_FILE" << 'EOF'

### Disk Space Alert
EOF

# Check if disk usage is above 80%
usage=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
if [ "$usage" -gt 80 ]; then
    echo "⚠️ **WARNING**: Disk usage is at ${usage}% - consider cleanup" >> "$REPORT_FILE"
else
    echo "✓ Disk usage is healthy at ${usage}%" >> "$REPORT_FILE"
fi

# Section 2: Git Repository Status
cat >> "$REPORT_FILE" << 'EOF'

---

## 2. Git Repository Status

### Main Data Repository
```
EOF

cd "$DATACORE_ROOT"
git status -s >> "$REPORT_FILE"
if [ $? -eq 0 ] && [ ! -s "$REPORT_FILE.tmp" ]; then
    echo "Clean working tree" >> "$REPORT_FILE"
fi

cat >> "$REPORT_FILE" << 'EOF'
```

**Branch:**
EOF
git branch --show-current >> "$REPORT_FILE"

cat >> "$REPORT_FILE" << 'EOF'

**Last Commit:**
```
EOF
git log -1 --oneline >> "$REPORT_FILE"

cat >> "$REPORT_FILE" << 'EOF'
```

### Space Repositories

EOF

# Check each space repository
for space in "$DATACORE_ROOT"/[0-9]-*; do
    if [ -d "$space" ] && [ -d "$space/.git" ]; then
        space_name=$(basename "$space")
        echo "#### $space_name" >> "$REPORT_FILE"
        echo '```' >> "$REPORT_FILE"
        cd "$space"

        # Get status
        status=$(git status -s)
        if [ -z "$status" ]; then
            echo "Clean working tree" >> "$REPORT_FILE"
        else
            echo "$status" >> "$REPORT_FILE"
        fi

        # Get branch
        branch=$(git branch --show-current 2>/dev/null)
        echo "Branch: $branch" >> "$REPORT_FILE"

        # Check if behind/ahead of remote
        git fetch --quiet 2>/dev/null
        ahead=$(git rev-list --count origin/${branch}..HEAD 2>/dev/null || echo "0")
        behind=$(git rev-list --count HEAD..origin/${branch} 2>/dev/null || echo "0")

        if [ "$ahead" != "0" ]; then
            echo "Ahead of origin by $ahead commits" >> "$REPORT_FILE"
        fi
        if [ "$behind" != "0" ]; then
            echo "Behind origin by $behind commits" >> "$REPORT_FILE"
        fi

        echo '```' >> "$REPORT_FILE"
        echo "" >> "$REPORT_FILE"
    fi
done

cd "$DATACORE_ROOT"

# Section 3: Running Services
cat >> "$REPORT_FILE" << 'EOF'

---

## 3. Running Services

### Expected Key Services
EOF

# Define expected services
declare -A expected_services=(
    ["nightshift"]="Overnight task execution"
    ["telegram-bot"]="Telegram integration"
)

cat >> "$REPORT_FILE" << 'EOF'

| Service | Status | Description |
|---------|--------|-------------|
EOF

# Check for nightshift
if ps aux | grep -E "nightshift.*run" | grep -v grep > /dev/null; then
    echo "| nightshift | ✓ Running | Overnight task execution |" >> "$REPORT_FILE"
else
    echo "| nightshift | ✗ Not Running | Overnight task execution |" >> "$REPORT_FILE"
fi

# Check for telegram bot
if ps aux | grep -E "telegram.*bot.py" | grep -v grep > /dev/null; then
    echo "| telegram-bot | ✓ Running | Telegram integration |" >> "$REPORT_FILE"
else
    echo "| telegram-bot | ✗ Not Running | Telegram integration |" >> "$REPORT_FILE"
fi

cat >> "$REPORT_FILE" << 'EOF'

### All Datacore-Related Processes
```
EOF

ps aux | grep -E "(datacore|nightshift|telegram)" | grep -v grep >> "$REPORT_FILE"

cat >> "$REPORT_FILE" << 'EOF'
```

EOF

# Section 4: Process Start Times
cat >> "$REPORT_FILE" << 'EOF'

---

## 4. Key Process Start Times

| Process | PID | Started | Uptime |
|---------|-----|---------|--------|
EOF

# Get process details for key services
ps -eo pid,lstart,etime,cmd | grep -E "(nightshift|telegram)" | grep -v grep | while read -r line; do
    pid=$(echo "$line" | awk '{print $1}')
    start=$(echo "$line" | awk '{print $2, $3, $4, $5, $6}')
    uptime=$(echo "$line" | awk '{print $7}')
    cmd=$(echo "$line" | awk '{for(i=8;i<=NF;i++) printf $i" "; print ""}' | cut -c1-50)
    echo "| $cmd | $pid | $start | $uptime |" >> "$REPORT_FILE"
done

# Section 5: System Information
cat >> "$REPORT_FILE" << 'EOF'

---

## 5. System Information

**Uptime:**
```
EOF

uptime >> "$REPORT_FILE"

cat >> "$REPORT_FILE" << 'EOF'
```

**Memory:**
```
EOF

free -h >> "$REPORT_FILE"

cat >> "$REPORT_FILE" << 'EOF'
```

**Load Average:**
```
EOF

cat /proc/loadavg >> "$REPORT_FILE"

cat >> "$REPORT_FILE" << 'EOF'
```

---

## Summary

EOF

# Generate summary
echo "- **Disk Usage:** ${usage}%" >> "$REPORT_FILE"

repos_clean=0
repos_dirty=0
for space in "$DATACORE_ROOT"/[0-9]-*; do
    if [ -d "$space/.git" ]; then
        cd "$space"
        if [ -z "$(git status -s)" ]; then
            ((repos_clean++))
        else
            ((repos_dirty++))
        fi
    fi
done

echo "- **Repositories:** $repos_clean clean, $repos_dirty with uncommitted changes" >> "$REPORT_FILE"

nightshift_status="Not Running"
if ps aux | grep -E "nightshift.*run" | grep -v grep > /dev/null; then
    nightshift_status="Running"
fi

telegram_status="Not Running"
if ps aux | grep -E "telegram.*bot.py" | grep -v grep > /dev/null; then
    telegram_status="Running"
fi

echo "- **Nightshift:** $nightshift_status" >> "$REPORT_FILE"
echo "- **Telegram Bot:** $telegram_status" >> "$REPORT_FILE"

cat >> "$REPORT_FILE" << 'EOF'

---

**Next health check:** One week from generation date
**Generated by:** server_health_check.sh (nightshift weekly task)
EOF

echo "Report generated: $REPORT_FILE"
