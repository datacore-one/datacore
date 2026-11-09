# agent-common.sh -- shared by agent-cutover, agent-new and agent-restore.
# Sourced, not run. The caller sets AGENT, NS, RELEASE, VALUES, IMAGE and
# AGENT_HOME (the agent's home inside its pod); OLD_HOST, OLD_HOME, SSH_OPTS
# and the unit/process settings only when an old host is involved.
# shellcheck shell=bash

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CHART=${CHART:-$HERE/chart/fleet}
NS=${NS:-firm}
RELEASE=${RELEASE:-fleet}
SSH_OPTS=${SSH_OPTS:-}
CLAIM_UNIT=${CLAIM_UNIT:-ledger-claim.service}
PROXY=${PROXY:-http://egress.shared.svc.cluster.local:3128}
HELPER=${HELPER:-$AGENT-helper}
TOOL=${TOOL:-${0##*/}}

say() { printf '%s\n' "$TOOL[$AGENT]: $*"; }
die() { say "STOP: $*" >&2; exit 1; }
# shellcheck disable=SC2086
old() { ssh -o BatchMode=yes $SSH_OPTS "$OLD_HOST" "$@"; }
kc() { kubectl -n "$NS" "$@"; }

need() {  # need VAR... -- every named setting must be non-empty
  for v in "$@"; do [ -n "${!v:-}" ] || die "$v is not set"; done
}

# A pod on the agent's volume, as the agent (uid 1000), HOME at the agent's
# home, leaving through the proxy -- for copying, cloning and setup steps.
helper_up() {
  kc get pod "$HELPER" >/dev/null 2>&1 && return
  kc run "$HELPER" --image="$IMAGE" --restart=Never --image-pull-policy=IfNotPresent \
    --overrides="{\"spec\":{\"securityContext\":{\"runAsUser\":1000,\"runAsGroup\":1000},
      \"automountServiceAccountToken\":false,
      \"containers\":[{\"name\":\"c\",\"image\":\"$IMAGE\",\"command\":[\"sleep\",\"infinity\"],
      \"env\":[{\"name\":\"HOME\",\"value\":\"$AGENT_HOME\"},
               {\"name\":\"HTTPS_PROXY\",\"value\":\"$PROXY\"},{\"name\":\"HTTP_PROXY\",\"value\":\"$PROXY\"},
               {\"name\":\"https_proxy\",\"value\":\"$PROXY\"},{\"name\":\"http_proxy\",\"value\":\"$PROXY\"},
               {\"name\":\"NO_PROXY\",\"value\":\"localhost,127.0.0.1\"},{\"name\":\"NODE_USE_ENV_PROXY\",\"value\":\"1\"}],
      \"volumeMounts\":[{\"name\":\"home\",\"mountPath\":\"$AGENT_HOME\"}]}],
      \"volumes\":[{\"name\":\"home\",\"persistentVolumeClaim\":{\"claimName\":\"$AGENT-home\"}}]}}" >/dev/null
  kc wait --for=condition=Ready "pod/$HELPER" --timeout=180s >/dev/null
  # ssh reads ~/.ssh from the passwd home (the image's /var/lib/agent), not
  # $HOME: point it at the agent's, as the chart does for the agent's pod.
  [ "$AGENT_HOME" = /var/lib/agent ] || in_vol sh -c "[ -e /var/lib/agent/.ssh ] || ln -s $AGENT_HOME/.ssh /var/lib/agent/.ssh"
}
in_vol() { kc exec -i "$HELPER" -- "$@"; }
helper_down() { kc delete pod "$HELPER" --ignore-not-found --wait=true >/dev/null; }

# The deployment exists, is at 0 replicas, and has its (empty or seeded) volume.
cluster_ready() {
  kubectl version >/dev/null || die "kubectl cannot reach the cluster"
  kubectl get ns "$NS" shared >/dev/null || die "namespaces $NS and shared must exist (playbook: install)"
  replicas=$(kc get deploy "$AGENT" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo missing)
  [ "$replicas" = "0" ] || die "deployment $NS/$AGENT must exist with 0 replicas (now: $replicas)"
  kc get pvc "$AGENT-home" >/dev/null || die "volume $AGENT-home missing"
}

old_ready() {
  old true || die "cannot reach $OLD_HOST over ssh"
  old sudo -n true || die "need passwordless sudo on $OLD_HOST to stop its system units"
}

# Stop and DISABLE every unit, cron line and process of the agent on its old
# host; nothing is deleted there.
freeze_old() {
  need OLD_HOST USER_UNITS SYSTEM_UNITS PROCESS_PATTERN
  say "stopping and disabling units on $OLD_HOST"
  [ "$USER_UNITS" = "-" ] || old "systemctl --user disable --now $USER_UNITS"
  [ "$SYSTEM_UNITS" = "-" ] || old "sudo systemctl disable --now $SYSTEM_UNITS"
  # Let a claim tick in flight finish rather than killing it mid-task.
  for i in $(seq 1 60); do
    old "systemctl is-active --quiet $CLAIM_UNIT" || break
    [ "$i" = 60 ] && die "$CLAIM_UNIT still running after 10 minutes"
    sleep 10
  done
  if [ -n "${CRON_KEEP:-}" ]; then
    # The old host keeps duties of its own (a chief of staff on his own box):
    # every active job line is the agent's except those matching CRON_KEEP;
    # environment lines (VAR=value) stay.
    old "{ [ -s ~/.datacore/state/crontab.pre-k3s ] || crontab -l > ~/.datacore/state/crontab.pre-k3s 2>/dev/null; }; awk -v keep='$CRON_KEEP' '/^[[:space:]]*(#|\$)/ || /^[A-Za-z_][A-Za-z0-9_]*=/ || \$0 ~ keep {print; next} {print \"# k3s-cutover # \" \$0}' ~/.datacore/state/crontab.pre-k3s | crontab -"
    left=$(old "crontab -l 2>/dev/null | grep -vE '^\s*(#|$)' | grep -vE '^[A-Za-z_][A-Za-z0-9_]*=' | grep -vE '$CRON_KEEP' || true")
  else
    # Lines tagged `# datacore-job:` are the agent's; any other active line stops the run.
    old "{ [ -s ~/.datacore/state/crontab.pre-k3s ] || crontab -l > ~/.datacore/state/crontab.pre-k3s 2>/dev/null; }; sed -E 's/^([^#].*# datacore-job:)/# k3s-cutover # \1/' ~/.datacore/state/crontab.pre-k3s | crontab -"
    left=$(old "crontab -l 2>/dev/null | grep -vE '^\s*(#|$)' || true")
  fi
  [ -z "$left" ] || die "cron lines still active on $OLD_HOST: $left"
  procs=$(host_procs)
  [ -z "$procs" ] || die "agent processes still running on $OLD_HOST: $procs"
  say "frozen: no unit, cron line or process of $AGENT is left on $OLD_HOST"
}

# The agent's processes on its old host -- not those of agents already in pods
# on that host (same uid; a chief of staff's box runs the cluster), and not this check.
host_procs() {
  old "for p in \$(pgrep -u \$(id -u) -f '$PROCESS_PATTERN'); do grep -q kubepods /proc/\$p/cgroup 2>/dev/null || ps -o pid=,args= -p \$p; done | grep -v -e pgrep -e 'for p in' || true"
}

thaw_old() {
  old "crontab ~/.datacore/state/crontab.pre-k3s"
  [ "$SYSTEM_UNITS" = "-" ] || old "sudo systemctl enable --now $SYSTEM_UNITS"
  [ "$USER_UNITS" = "-" ] || old "systemctl --user enable --now $USER_UNITS"
  say "old host running again"
}

# The signing key becomes a Secret: from the old host, or from KEY_FILE. It is
# moved, never generated here; fingerprints are compared.
key_secret() {
  if [ -n "${KEY_FILE:-}" ]; then
    src=$(sha256sum < "$KEY_FILE" | cut -c1-16)
    kc get secret "$AGENT-signing-key" >/dev/null 2>&1 \
      || kc create secret generic "$AGENT-signing-key" --from-file="$AGENT.key=$KEY_FILE" >/dev/null
  else
    need OLD_HOST OLD_HOME
    kc get secret "$AGENT-signing-key" >/dev/null 2>&1 \
      || old "cat $OLD_HOME/.datacore/keys/$AGENT.key" \
         | kc create secret generic "$AGENT-signing-key" --from-file="$AGENT.key=/dev/stdin" >/dev/null
    src=$(old "sha256sum < $OLD_HOME/.datacore/keys/$AGENT.key" | cut -c1-16)
  fi
  dst=$(kc get secret "$AGENT-signing-key" -o "jsonpath={.data.$AGENT\.key}" | base64 -d | sha256sum | cut -c1-16)
  [ "$src" = "$dst" ] || die "key fingerprint differs: source $src, secret $dst"
  say "signing key fingerprint $src identical on both sides"
}

# The start marker agent-prepare requires: written only once the agent runs
# nowhere else.
mark_runs_nowhere_else() {  # mark_runs_nowhere_else "<why>"
  helper_up
  in_vol sh -c "mkdir -p $AGENT_HOME/.datacore/state && printf '%s\n' \"$1 at $(date -u +%FT%TZ) by $TOOL\" > $AGENT_HOME/.datacore/state/k3s-cutover-frozen"
  say "start marker written: $1"
}

start_agent() {
  helper_down
  helm upgrade "$RELEASE" "$CHART" -n "$NS" -f "$VALUES" --set "agents.$AGENT.running=true" >/dev/null
  kc rollout status "deploy/$AGENT" --timeout=300s
  say "pod running"
}

stop_agent() {
  helm upgrade "$RELEASE" "$CHART" -n "$NS" -f "$VALUES" --set "agents.$AGENT.running=false" >/dev/null || true
  kc wait --for=delete pod -l "app.kubernetes.io/name=$AGENT" --timeout=120s >/dev/null 2>&1 || true
}
