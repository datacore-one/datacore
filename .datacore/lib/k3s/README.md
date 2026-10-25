# Datacore agents on k3s

Datacore agents as pods on one k3s node: one Deployment per agent, every
outbound connection through one proxy. A deployment's own values and cutover
settings (host names, addresses, crontabs) live in the operator's private
space, never in this repository.

```
images/agent-base/     what every agent image shares: OS, system Python, supercronic,
                       the agent user, the role lock and the start guard
images/agent-hermes/   Hermes runtime on the base (Tris, Winston)
images/agent-openclaw/ OpenClaw runtime on the base (Data)
images/egress/         squid, the agents' only way out
chart/fleet/           Helm chart: agents, proxy, network policies
bin/fleetctl           kill switch: stop-all, resume-all, status
bin/agent-cutover      move one agent from its host into its pod, step by step
bin/fleet-backup       nightly volume backup and restore (root, on the node)
tests/                 chart contract and kill-switch tests (pytest)
```

## The shape

- **One pod per agent, three containers on one volume.** `gateway` runs Hermes,
  `cron` runs the agent's duties with supercronic from the crontab in values,
  `stream` tails sessions to the agent-stream relay. One pod means one writer on
  the agent's git working copies and ledger chain, and the kill switch stops
  all of it with one replica count. Strategy `Recreate`: two pods of one agent
  never overlap.
- **State in the volume, code in the image.** The volume is the agent's old
  home directory at the same path (`defaults.home`), so every path in its config
  and scripts keeps working. `agent-prepare` (init container) links
  `~/.hermes/hermes-agent` and `~/.hermes/node` to `/opt` in the image.
- **Single-writer guard.** `agent-prepare` refuses to start unless
  `~/.datacore/state/k3s-cutover-frozen` exists, which only `agent-cutover
  adjust` writes, after `freeze` has proven nothing of the agent is left on the
  old host.
- **Egress.** Agent pods may reach only the proxy and cluster DNS (network
  policy). The proxy allows the listed domains, listed plain-HTTP and SSH
  destinations, and refuses private, tailnet, link-local and loopback ranges.
  With `openHttps: true` (research mode, Tris) any public https site is also
  allowed; every request is logged on the proxy's stdout. SSH leaves through
  the proxy via `ProxyCommand nc -X connect`.
- **The signing key** is a Secret that only the `key-install` init step reads
  (root, CHOWN only). It copies the key into memory, 0400 and owned by the
  agent, and the containers mount that copy read-only at its old path. Files
  listed in the cutover's `NEVER_COPY` (an owner's key found on the old host)
  are not copied: an agent's pod never holds them.
- **Ownership stays as it was on the old host.** No `fsGroup`: with it, the
  kubelet re-owns the whole home (files 660, dirs 2770), which ssh and
  Datacore's private-state check refuse. The `home-owner` init step (root, CHOWN
  only) gives the volume root to the agent at 0755, since the provisioner makes
  it root-owned and 0777. It also sets `/tmp` to 1777 like a host's.
- **ssh reads the passwd home**, not `$HOME`: when `defaults.home` differs from
  the image user's home, the volume's `.ssh` is also mounted at `imageHome`.

## Install on the box (once)

```sh
# k3s, single node, API on localhost and the tailnet only
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.31.4+k3s1 sh -s - \
  --disable traefik --disable servicelb \
  --cluster-cidr 10.42.0.0/16 --service-cidr 10.43.0.0/16 \
  --tls-san <tailnet-ip> --write-kubeconfig-mode 600
kubectl create namespace firm && kubectl create namespace shared
# images (no registry): build on the operator machine, then
docker build -t agent-base:1 images/agent-base
docker build --build-arg BASE=agent-base:1 -t agent-hermes:0.19.0-3 images/agent-hermes
docker build --build-arg BASE=agent-base:1 -t agent-openclaw:2026.9.3-1 images/agent-openclaw
docker save agent-hermes:0.19.0-3 agent-openclaw:2026.9.3-1 agent-egress:0.1.0 | gzip > fleet-images.tar.gz
bin/node-setup images fleet-images.tar.gz   # on the node: imports and PINS them
# Pinned because there is no registry: above 85% disk the kubelet deletes unused
# images, and an agent scaled to 0 (kill switch, backup) could not start again.
helm install fleet chart/fleet -n firm -f <private values file>
```

## Runtime templates

An agent's values name a runtime and supply only what is the agent's own:

```yaml
agents:
  data:
    runtime: openclaw          # or hermes
    signingKeySecret: data-signing-key
    signingKeyName: data.key
    storage: 20Gi
    crontab: |
      */15 * * * * ...
    # home: /home/gregor       # only for an imported agent that keeps its old home path
```

Image, gateway command, runtime environment, PATH and the list of state that git
does not hold come from `runtimes.<name>` in `values.yaml`; any of them can be
overridden per agent. A new runtime is a new image on `agent-base` (with its own
`prepare.d` steps) plus one `runtimes:` entry.

## Cutover (per agent)

`CUTOVER_ENV=<private settings file> bin/agent-cutover <step>`, checking each result:
`preflight`, `freeze`, `sync`, `key`, `copy`, `adjust`, `start`. Then verify in
this order: identity resolves; a signed ledger event verifies strictly; the
gate blocks a test call; a Telegram reply the owner sees; a claim tick; a
heartbeat; a refused internal address in the proxy log.

**Rollback:** `bin/agent-cutover rollback`. If the pod already wrote ledger
events, push them from the volume before the old
host is re-enabled, so the old host starts from the newest state.

## Kill switch

`bin/fleetctl stop-all` scales every agent to zero and remembers what ran;
`bin/fleetctl resume-all` restores exactly that.

## Backups

`bin/fleet-backup` stops the agent, tars its volume (about 7 minutes for a
7 GB volume) and starts it again. Keep the node's disk below 85%: a local copy
is as large as the volume, and at 90% the kubelet starts evicting pods.

## Not yet here

- Hermes is 0.19.0 from PyPI exactly as on the old host; the qualified patched
  runtime (`lib/hermes_runtime`) is the next image.
- The Datacore runner stays a git checkout in the volume (pinned, no
  self-update) instead of code in the image.
- Secrets other than the signing key stay in the volume as they were on the
  host; `creds sync --to-k8s` does not exist yet.
- One proxy allow-list per cluster, not per agent.
