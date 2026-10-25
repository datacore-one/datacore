# Fleet playbook

How to run Datacore agents as pods on one k3s node: install a node, add agents
(fresh from a runtime template, or imported from their own host), back up,
restore, move the whole fleet to another machine, and stop everything. Every
step has a command and a check; do the check before the next step.

Private things (host names, addresses, the values file, settings files) live in
the deployment's private space, never here. Paths below are relative to
`.datacore/lib/k3s/`.

## 0. What is where

| Piece | What it is |
|---|---|
| `images/agent-base` | OS, system Python, supercronic, agent user, role lock, start guard |
| `images/agent-hermes`, `images/agent-openclaw` | one image per runtime, on the base |
| `chart/fleet` | proxy, agents, network policy; `runtimes:` holds the templates |
| `bin/node-setup` | node install steps (root, on the node) |
| `bin/agent-new` | a fresh agent from a template; only its identity carried over |
| `bin/agent-cutover` | import an agent from its own host, home and all |
| `bin/agent-state` | back up what git does not hold; restore it anywhere |
| `bin/fleet-backup` | nightly backup on the node (uses agent-state) |
| `bin/agent-restore` | restore one agent from a backup into an empty volume |
| `bin/fleetctl` | kill switch: `stop-all`, `resume-all`, `status` |

An agent is: a values entry (runtime, identity, schedule), a signing-key Secret
(`<agent>-signing-key`), and a volume (`<agent>-home`). The pod runs three
containers (gateway, cron, stream) or one in standby.

## 1. Install a node

On the node, as root, with this directory copied to it:

```sh
bin/node-setup inspect                       # check: >= 8 GB RAM free, disk < 70%
bin/node-setup k3s <tailnet-ip>              # check: kubectl get nodes -> Ready
bin/node-setup lockdown                      # check: API not reachable from the public address
bin/node-setup namespaces                    # check: kubectl get ns firm shared
bin/node-setup images fleet-images.tar.gz    # check: images listed and "pinned"
bin/node-setup tools .                       # check: fleetctl status runs
bin/node-setup backup-timer                  # check: systemctl list-timers fleet-backup.timer
bin/node-setup killswitch <chief-of-staff-user>   # check: that user's fleetctl status works
```

Images are built on an operator machine (no registry; that is why they are pinned):

```sh
docker build -f images/agent-base/Dockerfile -t agent-base:3 .
docker build --build-arg BASE=agent-base:3 -t agent-hermes:0.19.0-5 images/agent-hermes
docker build --build-arg BASE=agent-base:3 -t agent-openclaw:2026.9.3-3 images/agent-openclaw
docker build -t agent-egress:0.1.0 images/egress
docker save agent-hermes:0.19.0-5 agent-openclaw:2026.9.3-3 agent-egress:0.1.0 | gzip > fleet-images.tar.gz
```

Then the chart, with every agent at `running: false`:

```sh
helm install fleet chart/fleet -n firm -f <private values>   # check: kubectl -n shared get pod (egress Running)
```

## 2. Add an agent fresh from a template (`agent-new`)

1. **Values**: add the agent with `runtime`, `signingKeySecret`, `signingKeyName`,
   `storage`, `crontab`, `running: false`; `helm upgrade`. Check: the volume
   `<agent>-home` exists, the deployment is at 0.
2. **Settings file** (private, `NEW_ENV`): `AGENT`, `VALUES`, `IMAGE`,
   `IDENTITY` (what carries over: e.g. `.datacore/identity.env .plur
   .openclaw/openclaw.json .openclaw/credentials`), `NEVER_COPY` (owner keys),
   `CLONES` (`path url [branch]` per line), `SETUP` (runtime config, e.g.
   registering the gate plugin from the runner checkout). With an old host:
   `OLD_HOST`, `OLD_HOME`, `USER_UNITS`, `SYSTEM_UNITS`, `PROCESS_PATTERN`.
   Without: `IDENTITY_DIR` and `KEY_FILE`.
3. Run each step and read its result:

```sh
NEW_ENV=<settings> bin/agent-new preflight   # empty volume, deployment at 0
NEW_ENV=<settings> bin/agent-new freeze      # old host only: units, cron lines, processes stopped
NEW_ENV=<settings> bin/agent-new key         # Secret from the existing key; fingerprints equal
NEW_ENV=<settings> bin/agent-new identity    # checksums equal; NEVER_COPY absent
NEW_ENV=<settings> bin/agent-new clone       # each checkout cloned through the proxy
NEW_ENV=<settings> bin/agent-new setup
NEW_ENV=<settings> bin/agent-new mark        # start marker: runs nowhere else now
NEW_ENV=<settings> bin/agent-new start       # rollout complete
```

4. **Record it**: set `running: true` for the agent in the values file and
   commit it. Every `helm upgrade` applies the whole file; an agent left at
   `running: false` there is stopped by the next upgrade of any agent.
5. **Verify**, in this order: identity resolves (`DATACORE_ACTOR`); a signed
   ledger event verifies strictly; the gate refuses a forbidden call and the
   refusal is in the ledger; a chat reply the owner sees; one run of each duty;
   `job_verify --machine <name>` green; a refused internal address in the proxy
   log. Rollback at any point: `bin/agent-new rollback`.

The key is always **moved**, never generated: the same key, so the agent's
ledger chain continues.

## 3. Import an agent from its own host (`agent-cutover`)

For an agent whose whole home moves (Tris, and Winston): settings in
`CUTOVER_ENV`, then `preflight`, `freeze`, `sync`, `key`, `copy`, `adjust`,
`start`, with the same verification as above. Rollback: `bin/agent-cutover
rollback`; push any ledger events the pod wrote before the old host runs again.

## 4. Back up

Nightly on the node (`fleet-backup.timer`, 03:50 UTC), or by hand:
`fleet-backup [AGENT]`. It holds **what git does not**: files outside checkouts,
files git does not track inside them (ignored config such as `.datacore/env`),
a patch of uncommitted edits, a bundle of unpushed commits, and a manifest of
every checkout. Caches and runtime install paths (`backupExclude`) are skipped.
The agent stops for the copy (about 2 minutes for Tris) and restarts.

Checks: `<UTC>.tar.gz` and `.sha256` in `/var/backups/fleet/<agent>/`; the
`<UTC>.log` beside it lists every checkout not fully pushed.

Off the node: pull the directory to the operator machine every night, e.g.
`rsync -a --rsync-path="sudo rsync" <node>:/var/backups/fleet/ ~/backups/fleet/`.

## 5. Restore one agent

Into an **empty** volume (a replaced volume, or a new machine), from an
operator machine:

```sh
bin/agent-restore <agent> <backup.tar.gz>    # state back, checkouts cloned at their commits
helm upgrade fleet chart/fleet -n firm -f <values> --set agents.<agent>.mode=standby --set agents.<agent>.running=true
```

The restored copy cannot start for real by itself: the start marker is removed,
because the original may still run. Check it in **standby** (no gateway, no
schedule, no stream): identity resolves, `ledger_cli verify` passes, the
runtime answers a one-shot prompt, the ledger heads equal the backup's
manifest. Only when the original is stopped for good: `agent-restore <agent>
--mark-live` (or restore with `--live` in the first place), then `helm upgrade`
without `mode=standby`.

## 6. Move the whole fleet to another machine

1. Install the new node (section 1) with the same values file, all agents at
   `running: false`.
2. On the old node: `fleetctl stop-all`, then `fleet-backup` (a final backup of
   each agent).
3. Copy the backups and each `signing-key.secret.yaml` to the operator machine.
4. On the new node: `agent-restore <agent> <file> --live` for each agent, then
   `helm upgrade` with `running: true`.
5. Verify every agent (section 2, step 4). Keep the old node stopped, not
   deleted, until the owner decides.

Never run one agent on two machines: two writers on one ledger chain and two
pollers of one chat bot.

## 7. Stop everything

`fleetctl stop-all` (or Winston's `/fleet_stop`). It remembers each agent's
replica count; `fleetctl resume-all` (`/fleet_resume`) restores exactly what was
running. A `helm upgrade` while stopped keeps the agents at 0: the chart reads
the kill switch's annotation. `fleetctl status` (`/fleet_status`) shows one line
per agent.

`fleetctl` and the backup run on the host (`/usr/local/bin/fleetctl`, a systemd
timer), not in a pod, so they keep working whichever agent is down -- including
the chief of staff. `fleetctl` needs the Kubernetes API. If the API itself does
not answer, the hard stop is on the host, as root: `/usr/local/bin/k3s-killall.sh`
stops every pod and k3s with them (start again with `systemctl start k3s`;
agents that were running come back).
