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

## Update: merged with the hub's audit fixes (2026-10-18)

`feat/agent-checkin` was built on `feat/ledger-hub` (a497b76), not on the role pool. So the hub branch is the parent that was merged in. **This branch does not contain `feat/ledger-role-pool`.** Nothing is pushed.

| Repo | Commit | What |
|---|---|---|
| root | 1f4984c | Merge of `feat/ledger-hub` at 3943110 |
| root | c279183 | Tests first: admission on the git path (red: 1 failed) |
| root | b14a10d | The git fallback reads through the hub's admission rule |
| app | aa52c00 | Merge of app `feat/ledger-hub` at 31b511c |

**How the conflicts were resolved.** In each conflict, both sides had added code, and both sides were kept:

- root `ledger/hub.py`: `presence` on `LocalTransport` and `HttpTransport`, next to the hub's `seal_request` and `reconcile`;
- app `api/ledger_hub.py`: the route list in the docstring;
- app `tests/test_ledger_hub_api.py`: the presence test, next to the seal and reconcile tests.

The `Offline` transport in `ledger/checkin.py` now also answers `seal_request` and `reconcile` as "unreachable".

**Check-ins pass the new admission.** Through the hub, a check-in is a signed `HubWriter` append. Its signed domain names this space, hub and log (audit finding 6). When the hub is down, the check reads the git copy through `ledger.presence.admitted_events`, the same admission rule the hub uses (findings 2 and 6). A check-in signed for another space, copied into this space's git copy, is no longer counted. A space without a hub is read as before, with a signature check on each check-in. `--dry-run` is unchanged and still tested.

**Tests changed:** the fixture enables the hub with an owner (finding 4) and gives the hub fixed policy inputs (finding 12). No assertion was changed. Two tests were added:

- `test_a_checkin_through_the_hub_is_signed_and_bound_to_this_space_and_log`;
- `test_a_checkin_signed_for_another_space_is_not_counted_from_the_git_copy`.

**Counts (seen in this session):**

| Suite | Result |
|---|---|
| `tests/test_ledger_agent_checkin.py` | 16 passed |
| `python3 -m pytest .datacore/lib/tests -k ledger -q` | 844 passed, 0 failed, 6277 deselected (hub baseline 828 + 16) |
| app `daemon/tests/test_ledger_hub_api.py` with `DATACORE_ROOT=<this worktree>` | 6 passed |

The presence checker was **not** run outside its tests.

**Owner steps, corrected for the hub's audit fixes.**

- Step 2 now needs an owner, and it needs every writer to declare support first:
  1. Run `python3 -m ledger.hub ready <space> --actor <writer>` on each writer's host.
  2. Then run `python3 -m ledger.hub enable <space> --hub-id <id> --grantor <hub writer> --owner <owner>`.
- *(Corrected 2026-10-18: the renewal task-hash fix is on `feat/ledger-hub`, 73a26f0 and 31eb47b, and merged into this branch. The earlier note saying it was missing was stale.)*

## Audit fixes, part 3

Source: the Codex re-audit of 2026-10-18 (`2-datacore/1-tracks/engineering/reports/2026-10-18-ledger-hub-codex-reaudit-report.md`), new defect 7, on top of the hub's part 3 (hub review note, items A–J).

Commits (root `feat/agent-checkin`): 0803f52 (merge of the hub fixes), 16d221b (tests first), 516b595 (fix), 9236c2d and 37fd2bf (merges of the hub's follow-ups). datacore-app `feat/agent-checkin`: 17d0c39 (merge of the app hub branch; one conflict in the API test file, both appended tests kept).

| Item | What was wrong | Fix | Tests (`test_ledger_agent_checkin.py`) |
|---|---|---|---|
| M (new 7) | A check-in whose payload time was a year ahead was reported ok with age zero, and it could name another agent | `presence.checkin_problem`: the agent must be the principal the writer writes for (or, with no principal, the writer itself), and the payload time within 10 minutes of the event's own HLC. The writer refuses anything else before appending (`bad_checkin`); the hub refuses it, also when the time is ahead of the hub's clock (`not_authorized`, so one already in a writer's log is kept as a rejected proposal); the git-copy read drops rejected proposals and applies the same rule. A last check-in ahead of the reader's clock is an `error` state with no age, never age zero | `test_reaudit_new7_a_checkin_from_the_future_or_for_another_agent_is_not_counted`, `test_reaudit_new7_a_checkin_ahead_of_the_readers_clock_is_an_error_not_age_zero` |

**Test fixture changed:** every HLC stamp now follows the test clock (as the pool tests do), so a check-in's time can be checked against its own HLC, as on a real host. All existing tests pass unchanged with it.

**Decision for the owner:** production agent names (miles, tris, data, winston) must equal the principal names their writers write for in `principals.yaml`, or each writer's own name. Not verified against the real registry; check it before enabling check-ins.

**Test counts (seen in this session):** check-in + hub suites: 85 passed after the fix. `-k ledger` on this branch: 845 passed before (4342f22), 859 passed and 1 failed after (at 37fd2bf): `test_ledger_done_archive.py::test_process_death_during_archive_recovers_before_retry` failed once while three suites ran in parallel, and passed 3 of 3 times when rerun alone (timing under load; not investigated further). App `test_ledger_hub_api.py` + `test_ledger_tasks.py` with `DATACORE_ROOT` at this worktree: 19 passed, 1 failed (the same pre-existing settled-read failure as on the hub branch). The wider runs of `-k "ledger or presence or checkin"` also show 4 failures in `test_fleet_presence_red_once.py`, which fail the same way on 31eb47b (a missing fleet-probe script in the test home); not caused here.
