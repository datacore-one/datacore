---
cadence: sprint-claim
role: cto
frequency: hourly
duration: 60min
tools: [Bash, Read, Edit, Write]
---

> Note on frequency: `hourly` matches Miles's actual heartbeat rhythm (~30 min between cycles per the heartbeat-* logs). At hourly, Miles can ship several PRs per active day. Relax to `every-2h` or `daily` if PR review queue overruns. Sprint 1 cap is unbounded so even bursty hourly claims work.

## Objective

Claim the next ready engineering item from the active PLUR Enterprise sprint and ship it. The bridge between `sprint.yaml` (sprint coordination layer) and Miles's autonomous heartbeat — runs each day, picks one item, works it.

## Context

Per `~/Data/docs/superpowers/specs/2026-05-07-plur-enterprise-sprint-execution-design.md`:
- Sprint state of truth: `~/Data/5-plur/2-projects/enterprise/sprints/<active>/sprint.yaml` (or flat `<active>.yaml` for older format)
- Claim helper: `~/Data/5-plur/2-projects/enterprise/scripts/claim.py`
- HITL gates: see CANVAS.md § HITL escalation list

## Steps

1. **Find the active sprint file** (matches both flat `2026-W*.yaml` files and
   subdirectory-based `2026-W*/sprint.yaml` files; never descends into
   `sprints/_archive/`, so archived sprints are excluded automatically):
   ```bash
   ENTERPRISE=~/Data/5-plur/2-projects/enterprise
   ACTIVE=$(ls -t "$ENTERPRISE"/sprints/2026-W*.yaml "$ENTERPRISE"/sprints/2026-W*/sprint.yaml 2>/dev/null | head -1)
   if [ -z "$ACTIVE" ]; then
     echo "No sprint files found; cadence skipped"
     exit 0
   fi
   echo "Active sprint: $ACTIVE"
   ```

2. **Find next ready engineering item** (skips if Miles already has an active claim, or if sprint not active):
   ```bash
   ITEM=$(python3 "$ENTERPRISE/scripts/claim.py" --find-next "$ACTIVE" \
     --actor miles-on-nightshift --owner-role engineering 2>/dev/null)
   RC=$?
   case $RC in
     0) echo "Next item: $ITEM" ;;
     6) echo "Sprint not active; cadence skipped"; exit 0 ;;
     7) echo "Already have active claim; finish it before claiming next"; exit 0 ;;
     8) echo "No ready engineering items; sprint backlog drained"; exit 0 ;;
     *) echo "Unexpected exit code $RC from claim.py --find-next"; exit 1 ;;
   esac
   ```

3. **Claim the item** (writes to sprint.yaml + creates/updates org task):
   ```bash
   ORG_FILE=~/Data/5-plur/org/next_actions.org
   python3 "$ENTERPRISE/scripts/claim.py" "$ACTIVE" "$ORG_FILE" "$ITEM" miles-on-nightshift
   ```

4. **Read the item details** for the work itself:
   ```bash
   python3 -c "
   from scripts.claim.sprint_io import load_sprint, find_item
   from pathlib import Path
   s = load_sprint(Path('$ACTIVE'))
   i = find_item(s, '$ITEM')
   print('TITLE:', i['title'])
   print('REF:', i.get('ref', ''))
   print('PRIORITY:', i['priority'])
   "
   ```

5. **Open a branch + work in the implementation repo**:
   - For `ref: github:plur-ai/<repo>#<n>` → clone or cd to that repo
   - **Branch from `origin/development`, and target it with the PR.** In
     `plur-ai/enterprise`, `development` is the integration branch and it
     auto-deploys to plur.datafund.io; `main` is the released record. Both are
     gated against you — `main` by classic protection (PR + 1 approval + status
     checks, no bypass), `development` by a ruleset that requires a PR from
     everyone except repository admins. You are `maintain`, not admin.
     `gh pr create` defaults to the repo's default branch — which is still
     `main` — so the base is **not** optional, you must pass it:
     ```bash
     git fetch -q origin
     git checkout -q -B "feat/<sprint_id>-<item_id>-<short>" origin/development
     # …implement, test…
     git push -q -u origin "feat/<sprint_id>-<item_id>-<short>"
     gh pr create --base development \
       --title "<type>(<scope>): <what changed>" \
       --body "Sprint: <sprint_id>, item: <item_id>
     Closes #<n>"
     ```
   - Implement, test (`pnpm test` or per-package equivalent) **before** pushing
     — a merge to `development` is a deployment to the server the team uses.
     See the enterprise repo's CLAUDE.md § "Shipping to plur.datafund.io".
   - Never `git push origin main` / `git push origin development`. Both reject
     a direct push from this account (enterprise#429). If one ever succeeds,
     stop and report it — it means the run is authenticated as an admin
     account rather than `miles-on-nightshift`, and the gate was bypassed
     rather than passed.

6. **HITL gate check** before any of:
   - merge of any PR (to `development` or `main`) — humans merge, you don't
   - `npm publish`
   - schema migration
   - production deploy
   - public comms post
   - infrastructure spend > €50

   STOP, post to Telegram with PR link + reason for HITL trigger, log entry to `sprint.yaml hitl_log:`. Wait for human approval. **Do not auto-merge.**

7. **PR open → state: review** (don't mark done; humans merge):
   - Edit sprint.yaml: backlog item state from `claimed` to `review`
   - Push the state-change commit

8. **Multi-actor / dependency block** — if you can't progress the item without input from another actor (e.g., B1 needs Crt for cross-user verify; B6 needs SMTP creds from plur9; B7 needs Tailscale auth from plur9):
   - Do every part you CAN do solo (write your half of the test, scaffold the cron without creds, document what's missing in a PR comment)
   - Edit sprint.yaml: state from `claimed` to `blocked`, add a `:BLOCKER:` note in the org task explaining what's needed and from whom
   - Post to Telegram with the block + who unblocks it
   - **Then immediately re-run step 2** (`claim.py --find-next`) and pick up the next ready item — don't sit idle until tomorrow's cadence
   - Repeat steps 3-8 for that item

9. **When PR merges** (separate cadence cycle, possibly next day):
   ```bash
   python3 "$ENTERPRISE/scripts/claim.py" --done "$ACTIVE" "$ORG_FILE" "$ITEM" \
     --result "<one-line summary>" --commit "<merge sha>"
   ```

10. **Continue until exhausted** — within the cadence's duration budget, keep cycling: claim → work → ship-or-block → next. Stop only when:
    - `--find-next` returns exit 8 (no ready items remaining for this role) — sprint backlog drained for engineering
    - `--find-next` returns exit 7 (in-flight cap reached — you have N items in `claimed` or `in-progress` and the sprint's `limits.max_in_flight_per_actor` is N). Items in `review` or `blocked` do NOT count — finish-then-park lets you claim the next one.
    - Cadence duration budget exhausted (60 min default; extend if a single item's PR is in flight)

## Output

- One backlog item claimed and PR opened (or status updated for ongoing work)
- Cadence log entry in `5-plur/.datacore/state/cadence-log.yaml`
- Heartbeat journal entry in `5-plur/journal/heartbeat-YYYY-MM-DD.log`

## Success criteria

Per cycle, one of:
- New PR opened against item's ref
- Existing PR advanced (commit pushed, comments addressed)
- Cadence skipped cleanly because no ready items / already have active claim / sprint not active

## Hard rules (inherited from Miles persona file)

- A task is not done until the org file says so. Implementation push without state push = not done.
- Silent abandonment breaks coordination. If blocked, mark `WAITING` with `:BLOCKER:`, post to Telegram.
- Personal craft engrams → `scope: agent:miles`. Cross-cutting findings → `global`.
- HITL gates pause work, log touchpoint, post to Telegram. Don't bypass.
