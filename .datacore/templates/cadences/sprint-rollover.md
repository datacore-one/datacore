---
cadence: sprint-rollover
role: cto
frequency: weekly
duration: 30min
tools: [Bash, Read, Write, Edit]
---

> Runs every Monday (weekly cadence). Idempotent: if a sprint file with status
> `planning` or `active` already exists for the current ISO week, the cadence
> exits without writing anything.

## Objective

Keep the PLUR Enterprise sprint train running without human bootstrapping:
when a new ISO week starts with no sprint on the books, carry over unfinished
work from the most recent sprint, draft the next sprint YAML in `planning`
status, and queue a `sprint_go` decision for the principal. **This cadence
NEVER activates a sprint** — activation is the principal's GO in the daily
session (or the auto-default after the decision's `default_at` SLA passes,
resolved by the decision pipeline, not by this cadence).

## Context

- Sprint state of truth: `~/Data/5-plur/2-projects/enterprise/sprints/2026-W*.yaml`
  (flat week files; `sprints/_archive/` is excluded — the flat glob never
  descends into it; per-sprint artifact folders like `2026-W23-sprint2/` hold
  canvas/retro docs)
- Sprint YAML schema: see `sprints/2026-W25-sprint.yaml` (fields the dashboard
  expects: sprint_id, project, status, cadence, dates, goal, okr_links,
  backlog[], stretch, ceremonies, done_when, hitl_log, claims)
- Heartbeat state: `~/Data/5-plur/.datacore/state/heartbeat.json`
  (`decisions_pending` is a list of structured dicts — see step 4)
- Decision schema: `PendingDecision` in
  `~/Data/.datacore/modules/ventures/lib/state_writer.py`

## Steps

1. **Check whether the current ISO week already has a live sprint** — compute
   dates with code, never from memory, and never with naive datetimes:
   ```bash
   ENTERPRISE=~/Data/5-plur/2-projects/enterprise
   python3 - "$ENTERPRISE" <<'EOF'
   import sys, glob, os
   from datetime import datetime, timezone
   import yaml

   enterprise = sys.argv[1]
   now = datetime.now(timezone.utc)
   iso = now.isocalendar()
   week_tag = f"{iso.year}-W{iso.week:02d}"   # e.g. 2026-W25

   live = []
   for path in glob.glob(os.path.join(enterprise, "sprints", "*-W*.yaml")):
       data = yaml.safe_load(open(path)) or {}
       if data.get("status") in ("planning", "active") and week_tag in os.path.basename(path):
           live.append(path)
   print(f"week={week_tag} live={live}")
   sys.exit(0 if live else 1)
   EOF
   RC=$?
   if [ $RC -eq 0 ]; then
     echo "Sprint already exists for current ISO week (planning|active); cadence done"
     exit 0
   fi
   ```

2. **Find the most recent sprint and collect carryover** — the newest
   `sprints/*-W*.yaml` by week tag (flat files only; `_archive/` excluded).
   Unfinished = backlog items whose `state` is NOT `done` and NOT `cancelled`.
   For each carried item:
   - keep `id` semantics by re-numbering into the new sprint (B1, B2, … in
     original order), set `carries_from: <old_sprint_id>#<old_id>`
   - reset `state: ready`, clear `claimed_by`, keep `org_id`, `ref`, `title`,
     `owner_role`, `priority`, `effort_estimate`, `acceptance`
   - update each item's `sprint_id:` to the new sprint id

3. **Draft the next sprint YAML** at `sprints/<year>-W<week>-sprint.yaml`:
   - `sprint_id` = file stem (the dashboard matches on it)
   - `status: planning` — **hard rule: this cadence never writes
     `status: active`**
   - `dates.start` = Monday of the current ISO week, `dates.end` = Sunday
     (verify day names via `python3 ~/Data/.datacore/lib/date_utils.py dow <date>`)
   - `goal:` = carry the previous sprint's goal prefixed with
     `Carryover draft — refine at GO:` (the principal edits at activation)
   - copy `ceremonies`, `facilitator`, `miles_routing`, `done_when` from the
     previous sprint; `hitl_log: []`, `claims: []`, `stretch: []`

4. **Queue the GO decision** in heartbeat.json — **merge, never clobber**
   (other writers' decisions must survive), and use tz-aware UTC timestamps:
   ```bash
   python3 - <<'EOF'
   import json
   from datetime import datetime, timedelta, timezone
   from pathlib import Path

   hb_path = Path.home() / "Data/5-plur/.datacore/state/heartbeat.json"
   hb = json.loads(hb_path.read_text()) if hb_path.exists() else {}
   pending = hb.get("decisions_pending") or []

   now = datetime.now(timezone.utc)
   sprint_id = "<NEW_SPRINT_ID>"   # filled in from step 3
   decision_id = f"sprint-go-{sprint_id}"

   if not any(isinstance(d, dict) and d.get("id") == decision_id for d in pending):
       pending.append({
           "id": decision_id,
           "kind": "sprint_go",
           "summary": f"Sprint {sprint_id} drafted in planning with carryover backlog — GO to activate?",
           "default_action": "activate",
           "default_at": (now + timedelta(hours=24)).isoformat(),
       })
   hb["decisions_pending"] = pending

   tmp = hb_path.with_suffix(".json.tmp")
   tmp.write_text(json.dumps(hb, indent=2, ensure_ascii=False) + "\n")
   tmp.replace(hb_path)
   print(f"queued decision {decision_id}")
   EOF
   ```

5. **Commit** the new sprint YAML in the enterprise repo with message
   `sprint-rollover: draft <sprint_id> (planning) + GO decision queued`.

## Output

- New `sprints/<year>-W<week>-sprint.yaml` with `status: planning` and
  carryover backlog (or a clean skip when a live sprint already exists)
- One `sprint_go` entry appended to heartbeat.json `decisions_pending`
- Cadence log entry in `5-plur/.datacore/state/cadence-log.yaml`

## Success criteria

One of:
- Skipped cleanly — a `planning`/`active` sprint already exists for this week
- Drafted next sprint in `planning` + queued exactly one `sprint_go` decision

## Hard rules

- **NEVER set `status: active`** — only the principal's GO (or the decision
  pipeline's auto-default resolution) activates a sprint.
- Never clobber `decisions_pending` — read-merge-write, dedupe on decision id.
- Never write naive datetimes — every timestamp is tz-aware UTC ISO 8601.
- Never type day-of-week names from memory — verify via date_utils.
- Don't touch `sprints/_archive/` and don't re-draft a week that already has
  a `closed`/`retro` sprint AND a successor in planning|active.
