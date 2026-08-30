# Datacore Smoke Tests

Run these by DOING, not by reading code. Each test exercises a real workflow
end-to-end and has a pass condition a script or a glance can check. Re-run the
full list after any change to nightshift, the CoS pipeline, the ledger, or the
state loop — and on a weekly cadence regardless.

Origin: 2026-08-30 audit directive ("test everything that is breaking by doing
it"). Goal: all Datacore workflows work and are robust.

Conventions: `NS` = nightshift host (Miles), `WN` = winston (CoS), `MAC` =
operator. Tests marked 💰 spend model tokens — run them deliberately.

| # | Workflow | Command | Pass condition |
|---|----------|---------|----------------|
| S1 | State-loop lint | `python3 .datacore/lib/org_state_lint.py --data-dir ~/Data` (MAC + NS) | `0 violation(s)` |
| S2 | Ledger round-trip | `DATACORE_ROOT=$PWD python3 .datacore/lib/ledger_checkpoint.py verify` (MAC + NS) | every space `restore identically`, 0 FAIL |
| S3 | v2 checklist | `sudo systemctl start v2-verify && tail -3 ~/.datacore/state/v2-verify.log` (WN) | `exit 0`, 0 FAIL |
| S4 | Queue build | `python3 -c "...build_queue(Path.home()/'Data')..."` (NS, read-only) | queue prints, modes correct, no traceback |
| S5 💰 | Task lifecycle | queue a tiny `:AI:content:` test task in `0-personal/org/nightshift.org` (NEXT), then `nightshift run --test` (NS) | task completes; output in `0-inbox/`; state DONE/REVIEW; Telegram line arrives |
| S6 💰 | Plan-only boundary | queue a task with `:ORIGIN: cadence`, no APPROVED_BY, then `nightshift run --test` (NS) | exactly one `nightshift-proposal-*.md`; zero commits/pushes/other writes; state REVIEW |
| S7 | Delegation gate | `cd ~/Data/.datacore/modules/chief-of-staff && python3 -m lib.cli run` (WN; `--dry-run` first) | `delegation gate: N approved, M parked...` line; fresh `cos-lastrun.json`; `delegation-gate.md` beside the brief |
| S8 | Approvals pipe | scripted submit → `/pending` shows it → `deny` (NS→WN, uses `COS_APPROVALS_*` from `.datacore/env/local.env`) | 200 / visible / 200 |
| S9 | Review routing | after any run with review items: `journalctl -u nightshift-overnight \| grep "routed to CoS approvals"` (NS) + item in WN pending | ≥1 routed line, item visible, no `submission failed` |
| S10 | Fleet sync | `python3 .datacore/lib/git_fleet_sync.py ~/Data --execute --pull` (every host) | every repo `clean (pulled)`; no PULL CONFLICT, no SKIP |
| S11 | Watchdog | `sudo systemctl start nightshift-watchdog && journalctl -u nightshift-watchdog -n 5` (NS) | completes; repeated alerts suppressed by 6h cooldown (`alert suppressed` lines) |
| S12 💰 | Podcast creation | research module → NotebookLM path: create a 1-source podcast (WN/MAC per module docs) | audio artifact produced and indexed |
| S13 | PLUR memory | `plur_doctor` via MCP (MAC) + `plur recall` CLI on NS | embedder + hybrid healthy; recall returns hits; no better-sqlite3 ABI errors |
| S14 | Digest + notify | `NIGHTSHIFT_NOTIFY_DRY=1 bash .datacore/modules/nightshift/server/nightshift-notify.sh` (NS) | composed digest with real stats, review names, action items |
| S15 | Parked branches | `git -C ~/Data/8-firm branch --list 'nightshift-parked-*' 'nightshift-park-*'` (NS, all spaces) | empty — a parked branch means a sync path broke again |
| S16 | State health | `python3 .datacore/modules/nightshift/lib/state_health.py ~/Data` (NS) | 0 stacked, 0 headerless, 0 orphaned QUEUED |
| S17 | DEFERRED wake | park a test task DEFERRED with `SCHEDULED: <yesterday>`, run S7 | task wakes to TODO, listed under "Woken" in gate report |
| S18 | Ledger ingest | `python3 .datacore/lib/ledger_ingest_org.py --root ~/Data` (MAC + NS) | `0 space(s) failed` |
| S19 | Stale git locks | `find ~/Data -path '*/.git/index.lock'` (every host) | empty — a lock older than 10min blocks every claim; runner + watchdog now self-heal, so a hit means the self-heal broke |
| S20 | Committed conflict markers | `grep -rlE '^(<<<<<<< \|>>>>>>> )' ~/Data/*/org/*.org ~/Data/*/notes/journals/*.md` (every host) | empty — committed markers make the pre-commit hook refuse EVERY commit, so all claims fail |
| S21 | Queue is fed | `python3 .datacore/modules/nightshift/lib/route_tasks.py ~/Data --dry-run` (NS) | routes a non-zero batch; if the queue is empty and this routes 0, the `/tomorrow` step never ran |
| S22 | Claim round-trip | probe: `touch .datacore/state/probe && git add … && git commit` in each space (NS) | commits cleanly — a hook rejection here is why claims fail, and the reason is now printed |
| S23 | Single runner | `pgrep -fc 'run.py .*/Data'` (NS) | ≤1 — two concurrent runners race claims; systemd dedups the service, manual runs do not |
| S24 | Enterprise CI reachable | `gh pr checks <open ladder PR> --repo plur-ai/enterprise` | Unit/DB tests reach a conclusion — 6h queue-then-cancel means Actions capacity, not a code failure |

## Cadence

- After every nightshift/CoS/ledger/state change: S1, S2, S4, S16, plus
  whatever the change touches.
- Weekly (fold into /gtd-weekly-review): full list.
- The 💰 tests double as real work when pointed at a genuinely queued small task.

## Log

| Date | Run by | Result |
|------|--------|--------|
| 2026-08-30 | Claude (audit session) | **PASS**: S1, S2 (after the state-normalisation fix), S3 (winston 21 ok/0 FAIL after genesis-fork repair), S4 (0 → 15 after routing), S7 (park + pending correct; HMM task parked live = issue #59 criterion 5), S8, S10, S11 (restart capability fired live), S15 (7 branches salvaged, rebase factory killed), S16, S17 (planted task woke), S18, S19, S20, S22. **PARTIAL**: S13 — local engine healthy, both remote stores down (expired token + unreachable host), 2 engrams queued safely. **FAIL→FIXED**: S5 (stale index.lock blocked every claim since 08:43; now self-heals), S23 (two runners raced — systemd dedups, manual runs do not). **Bugs found by testing, all fixed**: checkpoint state-normalisation, ledger genesis fork on winston, `_as_date` silent no-op, one bad node skipping a whole file, route_tasks writing retired states + a 5th header, stale-lock poisoning, swallowed commit-rejection reasons, AI-attribution trailers blocking enterprise PRs, `pull --rebase` manufacturing parked branches. |
