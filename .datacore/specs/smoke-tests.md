# Datacore Smoke Tests

Run these by DOING, not by reading code. Each test exercises a real workflow
end-to-end and has a pass condition a script or a glance can check.

Origin: an audit directive — "test everything that is breaking by doing it".
The first run found nine real bugs that no amount of code reading had
surfaced, which is the whole argument for this file.

Conventions: **NS** = executor host (overnight runs) · **WN** = chief-of-staff
server · **MAC** = operator workstation · **AGENT** = an agent host running a
forked runtime. The real host names live in `registry/infrastructure.yaml`
(untracked).
💰 marks tests that spend model tokens — run those deliberately.

---

## A. Health checks (fast, read-only — run these first)

| # | Workflow | Command | Pass condition |
|---|----------|---------|----------------|
| S1 | State-loop lint | `python3 .datacore/lib/org_state_lint.py --data-dir ~/Data` (MAC, NS, WN) | `0 violation(s)` |
| S2 | Ledger round-trip | `DATACORE_ROOT=~/Data python3 .datacore/lib/ledger_checkpoint.py verify` (MAC, NS, WN) | every space `restore identically`, 0 FAIL |
| S3 | v2 checklist | `sudo systemctl start v2-verify && tail -3 ~/.datacore/state/v2-verify.log` (WN) | `exit 0`, 0 FAIL |
| S16 | State health | `python3 .datacore/modules/nightshift/lib/state_health.py ~/Data` (NS) | 0 stacked, 0 headerless, 0 leftover QUEUED |
| S18 | Ledger ingest | `python3 .datacore/lib/ledger_ingest_org.py --root ~/Data` (MAC, NS) | `0 space(s) failed` |
| S13 | PLUR memory | `plur_doctor` (MAC) + `plur recall` (NS) | embedder + hybrid healthy; remote stores authenticated; outbox not growing |

## B. The things that silently jam everything

These four caused the worst outages found so far. All are cheap to check.

| # | Workflow | Command | Pass condition |
|---|----------|---------|----------------|
| S19 | Stale git locks | `find ~/Data -path '*/.git/index.lock'` (every host) | empty. A lock older than 10 min blocks EVERY claim — a crashed run poisoned 0-personal for a whole day. Runner + watchdog self-heal now, so a hit means the self-heal broke. |
| S20 | Committed conflict markers | `grep -rlE '^(<<<<<<< \|>>>>>>> )' ~/Data/*/org/*.org ~/Data/*/notes/journals/*.md` | empty. Committed markers make the pre-commit hook refuse every commit, so all claims fail. |
| S22 | Claim round-trip | probe in each space: `touch .datacore/state/probe && git add … && git commit` (NS) | commits cleanly. A hook rejection here is why claims fail; the reason is now printed. |
| S15 | Parked branches | `git -C ~/Data/<space> branch --list 'nightshift-park*'` (all spaces, NS) | empty. A parked branch means a sync path broke — `pull --rebase` used to manufacture one daily. |

## C. Nightshift (executor host)

| # | Workflow | Command | Pass condition |
|---|----------|---------|----------------|
| S21 | Queue is fed | `python3 .datacore/modules/nightshift/lib/route_tasks.py ~/Data --dry-run` (NS) | routes a non-zero batch. Empty queue + routes 0 ⇒ the `/tomorrow` step never ran. |
| S4 | Queue build | `build_queue(Path.home()/'Data')` (NS, read-only) | queue non-empty; each entry's `mode` correct; no traceback |
| S23 | Single runner | `pgrep -fc 'run.py .*/Data'` (NS) | ≤1. Two runners race claims; systemd dedups the service, manual runs do not (flock guard added). |
| S5 💰 | Task lifecycle | queue a small task, then let the service run (NS) | task claims, executes, writes `0-inbox/` output, ends DONE/REVIEW; batch-end commits AND pushes |
| S6 💰 | Plan-only boundary | queue a task with `:ORIGIN: cadence` and no `APPROVED_BY` | exactly one `nightshift-proposal-*.md`; zero other filesystem/git/network writes; state REVIEW |
| S27 | Evaluator non-votes | after a run: count exact `0.50` scores in the journal (NS) | near zero. An exact 0.50 is the timeout/crash sentinel, not a judgement — six of ~20 in one run invented two false reviews. |
| S14 | Digest + notify | `NIGHTSHIFT_NOTIFY_DRY=1 bash .datacore/modules/nightshift/server/nightshift-notify.sh` (NS) | real stats, review names, action items — not bare headers |
| S11 | Watchdog | `sudo systemctl start nightshift-watchdog` (NS) | completes; restarts a dead run when work is queued; repeat alerts suppressed by the 6h cooldown |

## D. Chief of Staff and the delegation gate

| # | Workflow | Command | Pass condition |
|---|----------|---------|----------------|
| S7 | Daily CoS run | `cd .datacore/modules/chief-of-staff && python3 -m lib.cli run` (WN; `--dry-run` first) | briefing + `delegation-gate.md` written; gate line printed; **0 errors**; fresh `cos-lastrun.json` |
| S17 | DEFERRED wake | plant a DEFERRED task with a past `SCHEDULED:`, run S7 | wakes to TODO, listed under "Woken" |
| S8 | Approvals pipe | submit → `/pending` → `deny` (NS→WN, creds in `.datacore/env/local.env`) | 200 / visible / 200 |
| S9 | Review routing | after a run with review items (NS) | ≥1 `routed to CoS approvals`; items visible in WN `/pending` with per-evaluator scores; no `submission failed` |
| S25 | Review backlog | `grep -chE '^\*+ REVIEW ' ~/Data/*/org/*.org` (NS); split by whether `:NIGHTSHIFT_OUTPUT:` is set | trending DOWN. Record a baseline the first time you run it; the briefing surfaces only the top 20. |

## E. Fleet, hosts and sync

| # | Workflow | Command | Pass condition |
|---|----------|---------|----------------|
| S10 | Fleet sync | `python3 .datacore/lib/git_fleet_sync.py ~/Data --execute --pull` (every host) | every repo `clean (pulled)`. Failures now come in three honest flavours: **PULL CONFLICT** (a real merge), **NO ACCESS** (this host's key is not authorised — a credential job, nothing to merge), **UNRELATED HISTORY** (the local checkout shares no ancestor with origin). SKIPs are repos on feature branches — a decision, not a fault. |
| S32 | No forked ledger logs | `python3 .datacore/lib/git_relay.py --forks` (EVERY machine — needs no SSH or remote) | `0 ledger file(s) with forked (actor,seq)`. Two events sharing one `(actor,seq)` is a fork, never a merge (DIP-0046). Union-merging a per-writer log creates them. Run this on every host — fork checking used to live only in the chief-of-staff server's twice-daily v2-verify, and the first fleet-wide run found forks on the executor host that nothing else would have caught. |
| S31 | No work trapped behind an access gap | `python3 .datacore/lib/git_relay.py --check` (MAC — the only machine that can reach both hosts and remotes) | `0 commit(s) trapped`. A host whose key is not authorised commits work it can never push, and that work sits invisible. `--host <name>` relays them. This is the mitigation, not the cure — the access gaps in S28 still want deploy keys. |
| S30 | Systemd units healthy | `systemctl --failed` (NS, WN) | **empty**. A failed unit is real until shown otherwise: a venv `PYTHONPATH` that hides the `datacore` package, a sync unit failing on staleness rather than on work at risk, or a timeout cap below a job's legitimate runtime. |
| S26 | Agent-fork lib drift | `ssh <agent-host> 'git -C ~/Data log -1 --format=%ci -- .datacore/lib'` | recent. Agent hosts that run FORKS carry `.datacore/lib` **vendored** — system fixes never reach them automatically. Sync the files they carry (`knowledge_commit.py`, `git_fleet_sync.py`) and commit in THEIR repo. |
| S28 | Every host can reach what it syncs | per host+repo: `git push --dry-run origin main` / `git pull --dry-run` | succeeds. Typical gaps: an HTTPS remote with nothing stored and `gh` not logged in, or an SSH key the remote has not authorised. Both are deploy-key/token jobs; work is committed but trapped locally meanwhile (S31 relays it). |

## F. Other workflows

| # | Workflow | Command | Pass condition |
|---|----------|---------|----------------|
| S29 | GTD state machine | `org_workspace_adapter.py update --file f.org --id X --state <each canon state>` | every canon state applies; DONE/CANCELLED refuse to reopen; **DEFERRED parks AND wakes** (the whole v2 park/wake design rests on this). Subcommand is `update`, not `set-state`. |
| S12 💰 | Podcast creation | `nlm_auth_sync.py check` → `nlm notebook list` → `audio get` → `audio download` | auth `ok` on ALL hosts (servers have no browser — `sync` to repair). If `audio download` fails on both the default path and `--direct-rpc` while audio plays in the UI, the break is upstream: update `nlm`. |

---

## Cadence

- **After any nightshift / CoS / ledger / state change**: section A + section B, plus whatever the change touches.
- **Weekly** (fold into `/gtd-weekly-review`): the full list.
- The 💰 tests double as real work when pointed at a genuinely queued small task.
