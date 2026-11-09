# agent-cutover settings for the rehearsal against the stand-in old host
# (tests/oldhost) and a throwaway k3d cluster (tests/fixtures/values-k3d.yaml).
AGENT=tris
OLD_HOST=agent@127.0.0.1
SSH_OPTS="-p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
OLD_HOME=/var/lib/agent
NS=firm
RELEASE=fleet
VALUES=$(dirname "$CUTOVER_ENV")/../fixtures/values-k3d.yaml
IMAGE=agent-hermes:0.19.0-4
USER_UNITS="hermes-gateway.service agent-update.timer"
SYSTEM_UNITS="ledger-claim.timer tris-heartbeat.timer"
PROCESS_PATTERN="hermes_cli|ledger_claim"
SYNC_CMD="cd /var/lib/agent && ./tris-sync.sh"
NEVER_COPY=".datacore/keys/gregor.key"
ADJUST_EXTRA='sed -i -E "s|^(git -C \"\\\$R\" fetch .*)$|# k3s: runner pinned, no self-update\n# \1|" /var/lib/agent/tris-sync.sh && grep -c "runner pinned" /var/lib/agent/tris-sync.sh'
