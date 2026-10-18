# Agent check-ins through the hub (review note, 2026-10-18)

Task 53c26c5c-f205-45e0-ac55-7b57ef68205c (in `2-datacore/org/inbox.org`). Builds on the ledger hub (review note `2026-10-18-ledger-hub.md`, branch `feat/ledger-hub`).

**Why.** On 2026-10-18 the 15-minute host probe (TCP 22) said Data's machine was up all morning while the Data agent itself was stuck (report `0-personal/content/reports/2026-10-18-nightshift-disk-full-report.md`, "Follow-up 2"). A host answering is not an agent working. Now each agent says it is working, as a signed ledger event, and Winston's check reads who stopped.

## What changed

| Repo | Branch | Files |
|---|---|---|
| datacore (root) | `feat/agent-checkin` (from `feat/ledger-hub`) | `ledger/events.py` (+`agent.checkin`), `ledger/log.py` (check-ins go to the telemetry log), `ledger/hub.py` (`Hub.presence`, `presence` on both transports), new `ledger/presence.py`, new `ledger/checkin.py`, new `agent_presence_check.py`, templates `agent-presence-check.service` / `.timer`, new `tests/test_ledger_agent_checkin.py` |
| datacore-app | `feat/agent-checkin` (from `feat/ledger-hub`) | `daemon/datacored/api/ledger_hub.py` (`GET /ledger/hub/{space}/presence`), `daemon/tests/test_ledger_hub_api.py` (+1 test) |

Tests were committed before the code in both repos. Nothing is pushed or deployed. No host was configured, and no crontab was edited. The host probe (`cos_fleet_probe.sh`) is unchanged.

## How it works

- **The check-in.** `agent.checkin` {agent, host, interval_s, ts} is an ordinary signed writer event. It goes through `HubWriter`, so it lands in the agent's own log first. If the hub is unreachable it is buffered and replayed on the next check-in that reaches the hub. It is a routine measurement, so it goes in the telemetry log (`.datacore/telemetry/<writer>.jsonl`), and the item fold ignores it.
- **The sender.** Each agent's existing heartbeat timer calls one command: `python3 -m ledger.checkin --space <space> --agent <name> --interval <s> [--hub-url URL --token-id <broker id>]`. The hub token comes from the credentials broker (`creds get`) at send time. If the broker serves nothing, the check-in is buffered.
- **Presence.** `ledger.presence.presence(events, now)` takes the newest check-in per agent **whose signature verifies**. It is ok up to 1.25 intervals old, late up to 2 intervals, and silent after that. An expected agent with no check-in is silent ("never checked in"). The hub answers this at `Hub.presence` and `GET /ledger/hub/{space}/presence`. Any git copy gives the same answer with `read_events`.
- **Winston's check.** `agent_presence_check.py check` asks the hub first. If the hub cannot be reached, it reads this machine's git copy, and the alert says so. For each agent that is not ok, it sends one alert through the host's **existing** alert route (`job_verify._send_command`, i.e. `command:` in `~/.datacore/alerts.yaml`; on Winston's host that is Winston's sender). No new Telegram client was added, and no credential is read here. An agent that was named stays in `~/.datacore/state/agent-presence-alerts.json` until it is ok again. Then one clear message is sent. If a send fails, nothing is recorded, so the next cycle retries. `--dry-run` prints the message instead of sending it.
- **Briefing.** `agent_presence_check.py briefing` (or `ledger.presence.briefing_lines`) prints one line per agent, e.g. `Data: silent, last check-in 6 h ago from data-host, expected every 30 min`. A line read from git is marked `[git copy]`.

## Verified in this session

- `tests/test_ledger_agent_checkin.py`: **14 passed**. That is the 13 DONE_WHEN tests plus a `--dry-run` safety test, run after the code. Before the code, the file failed at collection (red), as committed in the first commit.
- `daemon/tests/test_ledger_hub_api.py` with `DATACORE_ROOT` pointing at the root worktree: **4 passed**. Against the old route file the new test failed (1 failed, 3 passed), so it was red first.
- Full ledger suites (`cd .datacore/lib && python3 -m pytest -q tests/test_ledger_*.py`): **617 passed** in 15 min, 0 failed. This run included the check-in file at 13 tests, before the `--dry-run` test was added; that file alone then gave 14 passed.

## Limits (said plainly)

- **A check-in proves its writer, not its agent.** Any signed writer can name any agent. Binding an agent name to one writer key belongs in the principals registry. That is not done here.
- **The git fallback is only as fresh as the last pull** on Winston's machine. If the hub is down and the copy is stale, agents can look silent when they are not. The alert says it read from git.
- **A hub's own writer cannot check in through itself** (the hub refuses appends to its own log, `hub_log`). Winston's check-in must use Winston's writer name, not the hub actor.
- **Wiring into /today is not done.** The chief-of-staff module is its own repository. The one-line hook goes in `.datacore/modules/chief-of-staff/agents/system-pulse.md`, as a row in its command table: `| Agent check-ins | python3 .datacore/lib/agent_presence_check.py briefing --space ~/Data/<space> --agents miles,tris,data,winston |`.

## Incident during this build

A manual smoke run of `agent_presence_check.py check` on the workstation, against an empty temporary space, went through the workstation's real alert route (`~/.datacore/alerts.yaml`: ssh to Winston's host, `winston_send.py --alert`). It reported success. So one message was most likely sent to The Firm group: "⚠️ Agent not checking in: Miles: silent, never checked in. …". Delivery was **not verified** (the Telegram group was not checked). That is why `--dry-run` and its test were added. The run also used ssh to Winston's host indirectly, through that route.

## For the owner, to switch it on

1. Merge `feat/ledger-hub`, then `feat/agent-checkin`, in both repos (the app PR depends on the root one).
2. Choose the space that carries check-ins and opt it in to a hub: `python3 -m ledger.hub enable <space> --hub-id <id> --grantor <hub writer>`. Run datacored on the hub host with `DATACORE_LEDGER_HUB=1` and `DATACORE_HUB_ACTOR`.
3. Register the hub's bearer token with the broker (`creds add`, then `creds sync`) on each agent host and on Winston's host.
4. Make sure each agent host signs (`DATACORE_LEDGER_SIGN=1`) and has a declared actor.
5. Add one line to each agent's existing heartbeat timer: `python3 -m ledger.checkin --space … --agent <miles|tris|data|winston> --interval <its heartbeat seconds> --hub-url … --token-id …`.
6. On Winston's host, install `agent-presence-check.service` / `.timer` (or the cron line in the service file), filling in `<space>`. Run it once with `--dry-run` first.
7. Add the briefing row to `system-pulse.md` (above).
