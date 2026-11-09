---
cadence: sprint-rollover
role: cto
frequency: weekly
duration: 30min
tools: [Bash, Read, Write, Edit]
# DIP-0050: judged by this run's record, written during the run.
evidence:
  path: "1-tracks/ops/cadence-reports/sprint-rollover-{date}.md"
  require: ["^# Sprint Rollover", "^## Sprint"]
  min_bytes: 200
---

> Runs every Monday (weekly cadence). Idempotent: if this ISO week already has a
> sprint in `planning` or `active`, or the previous sprint is not closed yet, the
> cadence exits without drafting.

## Objective

Keep the PLUR Enterprise sprint train running without human bootstrapping:
when a new ISO week starts with no sprint on the books, carry over unfinished
work from the most recent sprint, draft the next sprint YAML in `planning`
status, and queue a `sprint_go` decision for the principal. **This cadence
NEVER activates a sprint** — activation is the principal's GO, never a default.

## Context

- **Read sprints from a fresh `origin/development`, never from the working
  checkout.** The checkout on the agent host can be weeks stale: on 2026-10-13
  this cadence copied `sprints/2026-W40-sprint.yaml`, a file deleted on
  development on 2026-09-29, and ignored the active sprint.
- Layout (`sprints/README.md`): `sprints/<sprint-id>/sprint.yaml`, id
  `<year>-W<week>-enterprise-sprint<N>`. Old flat `sprints/<id>.yaml` files are
  history; `sprints/_archive/` is excluded. Read both layouts, write only the
  directory one.
- Schema: `sprints/sprint.schema.json`; validate with
  `python scripts/validate_sprint.py <file>`. A closed sprint needs
  `dates.retro` and an explicit `carryover` list.
- Keep `project: plur-ai/enterprise` and the predecessor's `miles_routing`
  (overflow stays `plur-ai/enterprise`, never core).

## Steps

1. **Fetch and find the newest sprint on development.**
   ```bash
   ENTERPRISE=~/Data/5-plur/2-projects/enterprise
   git -C "$ENTERPRISE" fetch -q origin
   python3 - "$ENTERPRISE" <<'PY'
   import subprocess, sys, yaml
   from datetime import datetime, timezone
   repo = sys.argv[1]
   files = subprocess.run(["git", "-C", repo, "ls-tree", "-r", "--name-only",
                           "origin/development", "sprints/"],
                          capture_output=True, text=True, check=True).stdout.split()
   files = [f for f in files if f.endswith(".yaml") and "/_archive/" not in f
            and (f.endswith("/sprint.yaml") or f.count("/") == 1)]
   sprints = []
   for f in files:
       d = yaml.safe_load(subprocess.run(["git", "-C", repo, "show", f"origin/development:{f}"],
                          capture_output=True, text=True, check=True).stdout) or {}
       if d.get("sprint_id") and d.get("dates", {}).get("start"):
           sprints.append((str(d["dates"]["start"]), f, d.get("status"), d["sprint_id"]))
   sprints.sort()
   iso = datetime.now(timezone.utc).isocalendar()
   week = f"{iso.year}-W{iso.week:02d}"
   print("week", week)
   for s in sprints[-3:]: print(*s)
   PY
   ```
   Decide from the newest sprint (by `dates.start`), and stop at the first rule that applies:
   - **This week already has a sprint** (its id starts with this week's tag) and it is `planning`
     or `active` → exit: "already planned". Write the run record.
   - **The newest sprint is still `active` or `review`** → do NOT draft. Its close
     (status, `dates.retro`, `carryover`, retro) is the principal's call. Write the run
     record saying "predecessor <id> not closed, nothing drafted", and add one line to
     `5-plur/org/inbox.org` marked `[NEEDS_REVIEW]` asking the principal to close it.
   - **The newest sprint is `closed` without a `carryover` list** → same: report it, draft nothing.
   - Otherwise, draft from it (step 2).

2. **Carry over from the predecessor's `carryover` list, not from guesses.**
   Each item named in it is copied with its original id, scope, `acceptance`,
   `org`/`org_id` and `ref`; set `carries_from: <old_sprint_id>#<id>`; keep
   `state` (blocked stays blocked); clear claims only where the item was
   agent-claimed and not started. Never re-add an item whose PR has merged.
   Check each `ref` with `gh` before carrying it.

3. **Write `sprints/<year>-W<week>-enterprise-sprint<N+1>/sprint.yaml`**, N from
   the predecessor's id:
   - `status: planning`. **This cadence never writes `status: active`.**
   - `dates.start` is this week's Monday and `dates.end` its Sunday; verify both with
     `python3 ~/Data/.datacore/lib/date_utils.py dow <date>`.
   - `goal:` is the predecessor's, prefixed `Carryover draft, refine at GO:`.
   - Copy `facilitator` and `miles_routing` (update `sprint_tag_filter`), and
     `done_when`. Set `hitl_log` to one "Sprint GO" entry, not given; `claims: []`; `stretch: []`.
   - **Update `sprints/README.md`:** add the planning row to the index and point
     "Current sprint" at it.
   - Run `python scripts/validate_sprint.py <new file>`; a failure means no PR.

4. **Queue the GO decision with no default.** Merge into
   `~/Data/5-plur/.datacore/state/heartbeat.json` → `decisions_pending`
   (read-merge-write, dedupe on id, tz-aware UTC):
   `{"id": "sprint-go-<sprint_id>", "kind": "sprint_go", "summary": "...",
   "default_action": "none"}`, with no `default_at`. A sprint never activates by
   default: the principal declined auto-activation, and on 2026-10-13 a default
   of `activate` was queued on a draft that was wrong.

5. **Open a PR — never commit to `main` directly.**

   This step used to say "commit", and with `main` checked out that is what
   happened: five sprint rollovers went straight onto `main` with no PR and no
   review. On 2026-08-10 one of them reset the deployed server, because `main`
   still carried a deploy trigger and the box does `git reset --hard origin/main`
   — a planning artefact silently reverted two shipped fixes.

   Branch protection now backs this up (it did not on 2026-08-10 — the org was
   on a free plan then, which is how the incident happened). As of 2026-08-11
   the plur-ai org is on the **team** plan and both branches are gated:

   - `main` — classic protection: PR + 1 approval + 10 status checks, **no
     bypass for anyone**.
   - `development` — ruleset "development: non-admins open PRs": a PR is
     required, **repository admins bypass entirely**. Humans keep pushing and
     merging here freely; that is deliberate, it is where they develop.

   `miles-on-nightshift` holds `maintain`, not admin, so it bypasses neither.
   A direct push is rejected by GitHub, not merely discouraged — verified by
   attempting one: `GH013: Repository rule violations found`. Keep following
   this template anyway; a rejected push still costs a cycle, and the PR is
   the deliverable.

   ```bash
   cd "$ENTERPRISE"
   BR="sprint/rollover-<sprint_id>"
   git fetch -q origin
   # Branch from development — main is the released record, not the working branch.
   git checkout -q -B "$BR" origin/development
   git add sprints/<new-sprint-dir>/sprint.yaml sprints/README.md
   git commit -q -m "chore(sprint): draft <sprint_id> (planning) + GO decision queued"
   git push -q -u origin "$BR"
   gh pr create --base development --head "$BR" \
     --title "chore(sprint): draft <sprint_id> (planning)" \
     --body "Automated sprint rollover. Planning artefact only — no product code.
   Drafted by the nightshift agent; activation still requires the principal's GO."
   ```

   Leave the PR **open**. Do not merge it — an agent opening a PR is the point;
   merging it would put the review back where it was.

## Output

- New `sprints/<year>-W<week>-enterprise-sprint<N>/sprint.yaml` in `planning`,
  plus the index row, in one PR; or a clean exit naming the existing sprint or
  the unclosed predecessor
- One `sprint_go` entry with no default in heartbeat.json `decisions_pending`

## Success criteria

One of:
- Skipped cleanly — a `planning`/`active` sprint already exists for this week
- Predecessor not closed: reported, one inbox line for the principal, nothing drafted
- Drafted next sprint in `planning` + queued exactly one `sprint_go` decision

## Hard rules

- **NEVER commit or push to `main` or `development`.** Branch from
  `origin/development`, push the branch, open a PR against `development`, leave
  it open. Both are gated against this agent as of 2026-08-11 (enterprise#429),
  so a direct push is rejected — this rule tells you why, the platform enforces
  it. A push that unexpectedly *succeeds* means you are running as the wrong
  account: stop and report it.
- **NEVER set `status: active`**: only the principal's GO activates a sprint.
- **NEVER draft while the predecessor is `active` or `review`**, and never close
  a sprint yourself.
- Never clobber `decisions_pending` — read-merge-write, dedupe on decision id.
- Never write naive datetimes — every timestamp is tz-aware UTC ISO 8601.
- Never type day-of-week names from memory — verify via date_utils.
- Don't touch `sprints/_archive/` and don't re-draft a week that already has
  a `closed`/`retro` sprint AND a successor in planning|active.

## Run record

Write `1-tracks/ops/cadence-reports/sprint-rollover-YYYY-MM-DD.md`, headed `# Sprint Rollover — YYYY-MM-DD`, then `## Sprint`: the sprint file written for the ISO week, or "already planned" naming the existing file (the idempotent exit is a result, not a skip). The runner commits it and records the run; do not log the run anywhere else.
