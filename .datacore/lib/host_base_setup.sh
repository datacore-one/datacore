#!/usr/bin/env bash
# Base system for a new Ubuntu 24.04 host.
# Idempotent: run it again after every change, and on the replacement machine.
#
#   first run (root login still open, the user has no password yet):
#     ssh root@<new-ip> 'bash -s -- --user <user> --hostname <hostname> --sudo-nopasswd' < host_base_setup.sh
#   every later run (root login is closed by the first; sudo asks the owner):
#     scp host_base_setup.sh <host>:/tmp/ && ssh -t <host> sudo bash /tmp/host_base_setup.sh --user <user> --hostname <hostname> [--tailscale-only]
#
# Without --sudo-nopasswd the user's sudo asks for a password. That is the
# default on purpose: the Claude Code sessions run as this user, and passwordless
# sudo would hand every one of them root without the owner seeing it.
#
# Only the base: user, sshd, firewall, fail2ban, automatic security updates,
# git/tmux/mosh, Tailscale (installed, not logged in -- that is the owner's
# account). Python, Node, Claude Code, PLUR and Datacore are deliberately left
# out: the owner installs those as a test of the install process
# so that process gets exercised on a clean host.
set -euo pipefail
USER_NAME=""; HOST_NAME=""; TS_ONLY=0; NOPASSWD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --user) USER_NAME="${2:?}"; shift ;;
    --hostname) HOST_NAME="${2:?}"; shift ;;
    --tailscale-only) TS_ONLY=1 ;;
    --sudo-nopasswd) NOPASSWD=1 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac; shift
done
[ -n "$USER_NAME" ] && [ -n "$HOST_NAME" ] || { echo "--user and --hostname are required" >&2; exit 2; }
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 2; }
. /etc/os-release
[ "$ID" = ubuntu ] || { echo "Ubuntu only (found $ID)" >&2; exit 2; }
log() { echo "[base-setup] $*"; }

log "hostname and timezone"
hostnamectl set-hostname "$HOST_NAME"
timedatectl set-timezone UTC

log "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git tmux mosh ufw fail2ban unattended-upgrades curl ca-certificates >/dev/null
dpkg-reconfigure -f noninteractive unattended-upgrades

log "user $USER_NAME"
id "$USER_NAME" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$USER_NAME"
usermod -aG sudo "$USER_NAME"
if [ "$NOPASSWD" = 1 ]; then
  echo "$USER_NAME ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-$USER_NAME"
  chmod 440 "/etc/sudoers.d/90-$USER_NAME"
else
  passwd -S "$USER_NAME" | awk '{exit ($2 == "P") ? 0 : 1}' \
    || { echo "$USER_NAME has no password: set one (sudo passwd $USER_NAME) before dropping passwordless sudo" >&2; exit 1; }
  rm -f "/etc/sudoers.d/90-$USER_NAME"
fi
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "/home/$USER_NAME/.ssh"
if [ -s /root/.ssh/authorized_keys ] && [ ! -s "/home/$USER_NAME/.ssh/authorized_keys" ]; then
  install -m 600 -o "$USER_NAME" -g "$USER_NAME" /root/.ssh/authorized_keys "/home/$USER_NAME/.ssh/authorized_keys"
fi
[ -s "/home/$USER_NAME/.ssh/authorized_keys" ] || { echo "no SSH key for $USER_NAME -- refusing to lock down sshd" >&2; exit 1; }

log "sshd: keys only, no root login"
cat > /etc/ssh/sshd_config.d/10-hardening.conf <<EOF
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
AllowUsers $USER_NAME
EOF
sshd -t && systemctl reload ssh

log "tailscale (installed; login is the owner's step)"
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh

log "firewall"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow in on tailscale0 >/dev/null
if [ "$TS_ONLY" = 1 ]; then
  tailscale status >/dev/null 2>&1 || { echo "tailscale is not up -- refusing to close public SSH" >&2; exit 1; }
  ufw delete allow OpenSSH >/dev/null 2>&1 || true
  log "public SSH closed; reachable over the tailnet only"
else
  ufw allow OpenSSH >/dev/null
fi
ufw --force enable >/dev/null

log "fail2ban"
# Tailnet addresses are the owner's own machines: one misconfigured job on one
# of them (2026-10-25: a 5-second rsync as a user sshd does not allow) banned
# the whole Mac. Strangers get ten tries per ten minutes before a ban.
cat > /etc/fail2ban/jail.d/20-datacore.local <<EOF
[DEFAULT]
ignoreip = 127.0.0.1/8 ::1 100.64.0.0/10 fd7a:115c:a1e0::/48
maxretry = 10
findtime = 10m
bantime = 10m
EOF
systemctl enable fail2ban >/dev/null
systemctl restart fail2ban

log "done: $(hostname), user $USER_NAME, ufw $(ufw status | head -1 | cut -d' ' -f2), tailscale $(tailscale status >/dev/null 2>&1 && echo up || echo 'installed, not logged in')"
