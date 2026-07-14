#!/usr/bin/env bash
# Install the datacore-fleet-sync timer on an agent host.
#
# Without this, agents silently re-strand. Neither hermes nor plur-claw had ANY
# cron entry or systemd timer touching git — which is why Tris accumulated 53
# uncommitted competitor scans over two months and nobody ever saw them, and why
# Mr Data's work sat invisible on a stray branch.
#
# Runs twice daily. Bidirectional: pulls each default-branch repo onto latest,
# then commits and pushes whatever the agent produced. Repos on a non-default
# branch are held back — those need a human decision, not a sweep.
#
# Usage: install_fleet_sync_timer.sh <RUN_AS_USER> <DATA_DIR>
set -euo pipefail

RUN_AS="${1:?need user}"
DATA_DIR="${2:?need data dir}"

cat > /etc/systemd/system/datacore-fleet-sync.service <<EOF
[Unit]
Description=Datacore fleet sync — land agent work, pull latest shared knowledge
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${RUN_AS}
WorkingDirectory=${DATA_DIR}
ExecStart=/usr/bin/python3 ${DATA_DIR}/.datacore/lib/git_fleet_sync.py ${DATA_DIR} --execute --pull
TimeoutStartSec=900
EOF

cat > /etc/systemd/system/datacore-fleet-sync.timer <<'EOF'
[Unit]
Description=Run datacore fleet sync twice daily

[Timer]
# 06:10 and 18:10 UTC. Offset from the nightshift batch windows so the sync is
# not fighting a run that is mid-commit in the same repos.
OnCalendar=*-*-* 06:10:00
OnCalendar=*-*-* 18:10:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now datacore-fleet-sync.timer >/dev/null 2>&1

echo "  installed: $(systemctl is-enabled datacore-fleet-sync.timer) / $(systemctl is-active datacore-fleet-sync.timer)"
systemctl list-timers datacore-fleet-sync.timer --no-pager 2>/dev/null | sed -n 2p
