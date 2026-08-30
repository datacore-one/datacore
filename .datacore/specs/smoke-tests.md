# Datacore Smoke Tests

Run these by DOING, not by reading code. Each test exercises a real workflow
end-to-end and has a pass condition a script or a glance can check.

Origin: the 2026-08-30 audit directive — "test everything that is breaking by
doing it". That first run found nine real bugs that no amount of code reading
had surfaced, which is the whole argument for this file.

Conventions: **NS** = nightshift host (Miles) · **WN** = winston (CoS) ·
**MAC** = operator · **CLAW/HERMES** = Mr Data / Tris.
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
| S15 | Parked branches | `git -C ~/Data/8-firm branch --list 'nightshift-park*'` (all spaces, NS) | empty. A parked branch means a sync path broke — `pull --rebase` used to manufacture one daily. |

## C. Nightshift (Miles)

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

## D. Winston (Chief of Staff) and the delegation gate

| # | Workflow | Command | Pass condition |
|---|----------|---------|----------------|
| S7 | Daily CoS run | `cd .datacore/modules/chief-of-staff && python3 -m lib.cli run` (WN; `--dry-run` first) | briefing + `delegation-gate.md` written; gate line printed; **0 errors**; fresh `cos-lastrun.json` |
| S17 | DEFERRED wake | plant a DEFERRED task with a past `SCHEDULED:`, run S7 | wakes to TODO, listed under "Woken" |
| S8 | Approvals pipe | submit → `/pending` → `deny` (NS→WN, creds in `.datacore/env/local.env`) | 200 / visible / 200 |
| S9 | Review routing | after a run with review items (NS) | ≥1 `routed to CoS approvals`; items visible in WN `/pending` with per-evaluator scores; no `submission failed` |
| S25 | Review backlog | `grep -chE '^\*+ REVIEW ' ~/Data/*/org/*.org` (NS); split by whether `:NIGHTSHIFT_OUTPUT:` is set | trending DOWN. 2026-08-30 baseline: 242 total — 208 real, 13 zombie — while the briefing surfaces 20. |

## E. Fleet, hosts and sync

| # | Workflow | Command | Pass condition |
|---|----------|---------|----------------|
| S10 | Fleet sync | `python3 .datacore/lib/git_fleet_sync.py ~/Data --execute --pull` (every host) | every repo `clean (pulled)`. Failures now come in three honest flavours: **PULL CONFLICT** (a real merge), **NO ACCESS** (this host's key is not authorised — a credential job, nothing to merge), **UNRELATED HISTORY** (the local checkout shares no ancestor with origin). SKIPs are repos on feature branches — a decision, not a fault. |
| S31 | No work trapped behind an access gap | `python3 .datacore/lib/git_relay.py --check` (MAC — the only machine that can reach both hosts and remotes) | `0 commit(s) trapped`. A host whose key is not authorised commits work it can never push; on 2026-08-30 **166 commits** (135 of them winston's 5-plur work) were sitting invisible. `--host <name>` relays them. This is the mitigation, not the cure — the access gaps in S28 still want deploy keys. |
| S30 | Systemd units healthy | `systemctl --failed` (NS, WN) | empty, or every entry explained. 2026-08-30: `coinglass-scraper` fixed (venv PYTHONPATH hid `datacore`); `datacore-fleet-sync` fails honestly on the access gap below; `plur-geo-scan` exceeds its 2h cap (PLUR app, outside Datacore). |
| S26 | Agent-fork lib drift | `ssh <claw\|hermes> 'git -C ~/Data log -1 --format=%ci -- .datacore/lib'` | recent. Mr Data and Tris run FORKS with `.datacore/lib` **vendored** — system fixes never reach them automatically. Sync the files they carry (`knowledge_commit.py`, `git_fleet_sync.py`) and commit in THEIR repo. |
| S28 | Every host can reach what it syncs | per host+repo: `git push --dry-run origin main` / `git pull --dry-run` | succeeds. **Open access gaps 2026-08-30**: winston cannot PUSH `1-datafund` (datafund/datafund-space) or `3-fds` (fairDataSociety/fds-space) — HTTPS remotes, nothing stored, `gh` not logged in, SSH key rejected. nightshift cannot PULL `DHF` (datafund/DHF), `website`, `extract-cli` — key not authorised. Both are deploy-key/token jobs; work is committed but trapped locally meanwhile. |

## F. Other workflows

| # | Workflow | Command | Pass condition |
|---|----------|---------|----------------|
| S29 | GTD state machine | `org_workspace_adapter.py update --file f.org --id X --state <each canon state>` | every canon state applies; DONE/CANCELLED refuse to reopen; **DEFERRED parks AND wakes** (the whole v2 park/wake design rests on this). Subcommand is `update`, not `set-state`. |
| S12 💰 | Podcast creation | `nlm_auth_sync.py check` → `nlm notebook list` → `audio get` → `audio download` | auth `ok` on ALL hosts (servers have no browser — `sync` to repair). **Known upstream break 2026-08-30**: `audio download` and `audio share` fail on both the default path AND `--direct-rpc`; audio generates and plays in the UI but cannot be fetched. Needs a newer `nlm`. |
| S24 | Enterprise CI reachable | `gh pr checks <open ladder PR> --repo plur-ai/enterprise` | Unit/DB tests reach a conclusion. 6h queue-then-cancel means Actions capacity, not a code failure. |

---

## Cadence

- **After any nightshift / CoS / ledger / state change**: section A + section B, plus whatever the change touches.
- **Weekly** (fold into `/gtd-weekly-review`): the full list.
- The 💰 tests double as real work when pointed at a genuinely queued small task.

## Log

| Date | Run by | Result |
|------|--------|--------|
| 2026-08-30 (S6) | Claude (audit session) | **S6 ✓ — issue #60 acceptance criterion 1 verified in production.** A machine-originated task with no `APPROVED_BY` was classified `PLAN-ONLY` by the live queue builder, executed under the read-only tool allowlist, and produced **exactly one** `nightshift-proposal-*.md` (105 lines, correct `## 1. Gist / ## 2. Analysis / ## 3. Intended Actions` structure). Total files changed across the whole execution: the proposal, the task's own org state, and the ledger mirror of that state — **nothing else**, no network or code mutations. Task ended REVIEW. One defect found and fixed: the queue-row `gist` had captured the model's preamble instead of the Gist section. Suite: **170 passed, 0 failed**. |
| 2026-08-30 (late) | Claude (audit session) | Full run end-to-end: 3 executed, 1 approved, 2 review, 0 failed, 2h2m, ~$0.14. **S5 ✓ S9 ✓ S14 ✓** — both review items routed to Winston AND the summary names the disagreement: `ceo 0.50, coo 0.50, critic 0.62, archivist 0.66, user 0.72, cto 0.92 · routed to Winston`. Both were manufactured by evaluator timeouts: excluding non-votes they average 0.73 and 0.79 = approved_with_notes. Fixed for the next run (S27). Batch-end committed AND pushed cleanly. Nightshift suite: **166 passed, 0 failed**. |
| 2026-08-30 | Claude (audit session) | **PASS**: S1, S2 (after the state-normalisation fix), S3 (winston 21 ok/0 FAIL after genesis-fork repair), S4 (0 → 15 after routing), S7 (park + pending correct; HMM task parked live = issue #59 criterion 5), S8, S10, S11 (restart fired live), S15 (7 branches salvaged, rebase factory killed), S16, S17 (planted task woke), S18, S19, S20, S22, S29. **PARTIAL**: S13 — local engine healthy, both remote stores down (expired token), 2 engrams queued safely. S12 — auth repaired on all hosts, audio download broken upstream. **FAIL→FIXED**: S5 (stale index.lock blocked every claim since 08:43), S23 (two runners raced). **Bugs found by testing, all fixed**: checkpoint state-normalisation, ledger genesis fork on winston, `_as_date` silent no-op, one bad node skipping a whole file, route_tasks writing retired states + a 5th header, stale-lock poisoning, swallowed commit-rejection reasons, evaluator non-votes inventing reviews, AI-attribution trailers blocking enterprise PRs, `pull --rebase` manufacturing parked branches, auth/quota failures misclassified as retryable. |
