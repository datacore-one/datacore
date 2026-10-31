#!/usr/bin/env python3
"""process_views — the fleet's processes drawn as readable swimlanes, next to the architecture map.

Owner, 2026-10-30: "overview is becoming more difficult ... expand it so that also
just processes and workflows can be analysed, for example a task's lifecycle ...
make the diagram easily readable, not small fonts."
Second round, same day: a left sidebar with every flow, type one size smaller,
the ledger's own processes (fold, seal, corrections), where signing and
provenance receipts happen, and how cadences and scheduled jobs run.

Each flow below is written from the code and the schedules (manifest.yaml job
names, scripts, times in UTC), not from memory; the overlap view is generated
from processes.yaml (process_inventory.py). Lanes are columns and time runs down
the page, so a diagram stays near screen width and its type stays large: node
names 18px, technical lines 14px.

Diagram style: the diagram-design skill, plur profile (Outfit, Literata,
JetBrains Mono for technical lines only; one or two cyan focal nodes).

    process_views.py [--inventory processes.yaml] [--out processes.html] [--open]
"""
from __future__ import annotations

import argparse
import html
import subprocess
from pathlib import Path

import yaml

from process_upgrades import UPGRADES

ROOT = Path(__file__).resolve().parents[2]
ARCH = ROOT / "2-datacore" / "1-tracks" / "dev" / "architecture"

# -- geometry (4px grid) ------------------------------------------------------------
LANE_W = 336          # one actor column; 4 lanes + padding = 1392px, the page width
BOX_W, BOX_H = 272, 96
MAX_NAME, MAX_SUB = 26, 28   # characters that fit a box at 16px / 13px mono (kept from 18/14: room to spare)
ROW = 176             # step pitch; the 80px gap holds a handoff and its label
TOP = 96              # lane header band
PAD = 24


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


# -- marks: where a step is signed, co-signed or leaves a provenance receipt ---------
# code -> (always|optional, letter, hover text). Lower-case codes are optional and
# drawn dashed; the letter is what the badge shows.
MARKS = {
    "S": ("always", "S", "Signed by the writer's own key. In a hub space (all ten since activation) an unsigned event is not admitted."),
    "O": ("always", "O", "Signed by the owner (the Mac's key): hub.enable, policy.set, reversals and forward corrections."),
    "c": ("optional", "C", "Needs your co-sign (approval.grant) only when the task has a side effect such as email.send, payment or prod.deploy (ledger/policy.py)."),
    "r": ("optional", "R", "Provenance receipt on Swarm: fingerprints only, signed by the gateway's notary. Only where the receipt hook (agent turns) or the git commit hook is installed."),
}

# -- flows (from the code; see each source note) -----------------------------------
# step: (id, lane, row, name, technical line, state, "focal"|"", marks)
FLOWS = [
    {
        "slug": "task-lifecycle",
        "group": "Work",
        "title": "A task's lifecycle",
        "lede": ("From capture to done, as it runs today. An agent gets work by one of two routes that "
                 "run side by side: assigned to it on the ledger (picked up within 15 minutes), or queued "
                 "in nightshift.org for Miles (hourly). Winston's 23:30 review gates only tasks a machine made."),
        "source": "org_workspace_adapter.py add (item.create) · ledger_publish_safe.py (mac-ledger-publish, after every join) · "
                  "datacored ledger hub on the box · ledger_claim.py --execute (box-ledger-claim :05/:20/:35/:50, plur-claw every 15 min, "
                  "miles and tris on their own hosts) · nightshift/lib/task_queue.py (MACHINE_ORIGINS, APPROVED_BY) · "
                  "nightshift run (manifest: hourly) · chief-of-staff delegation_gate.py (box-delegation-review 23:30) · ledger/policy.py",
        "lanes": ["YOU · MAC", "SHARED LEDGER · HUB ON THE BOX", "CLAIMS · WINSTON, DATA, TRIS", "MILES · NIGHTSHIFT QUEUE"],
        "steps": [
            ("cap", 0, 0, "You add or assign a task", "org adapter · item.create", "TODO", "", "Sc"),
            ("pub", 1, 1, "On the shared ledger", "published after your join", "", "focal", ""),
            ("clm", 2, 2, "Assigned agent claims it", "every 15 min · claim, run", "", "", "Sr"),
            ("gate", 3, 2, "Approval check", "machine-made: needs Winston", "", "focal", ""),
            ("run", 3, 3, "Miles runs it", "nightshift run · hourly", "", "", "Sr"),
            ("res", 1, 4, "Result recorded", "item.complete", "REVIEW", "", "S"),
            ("ok", 0, 5, "You check it", "item.verify", "DONE", "", "S"),
        ],
        "edges": [("cap", "pub", "PUBLISH"), ("pub", "clm", ":ASSIGNEE:"), ("pub", "gate", "NIGHTSHIFT.ORG"),
                  ("gate", "run", "YOURS, OR APPROVED"), ("clm", "res", ""), ("run", "res", ""), ("res", "ok", "TO REVIEW")],
        "notes": [
            "A task you assign to Data during the day is not left for the next day. It waits only for your Mac to publish it, which happens after each join rather than on a clock, and then for Data's claim job, which runs every 15 minutes. A task with a side effect (email, payment, deploy) is never claimed unattended: it needs your grant to exist and is refused at claim.",
            "Two routes do the same job: ledger assignment (the claim loop of DIP-0038) and the nightshift.org queue (DIP-0009, DIP-0011). Miles takes work from both. Merging them into one route is the clearest simplification on this page.",
            "Winston's 23:30 review approves only tasks a machine made (from a cadence, a follow-up or an agent). Your own tasks never wait for it. Not verified: whether a missing or stale review receipt also holds approved work; that depends on a setting on Miles's machine.",
            "datacored, the Datacore app's daemon, runs on the box as the ledger hub: it admits every signed event, grants claims (leases) and seals each space every 15 minutes. See 'How an event becomes final'.",
            "A failed run never closes a task: it goes back to NEXT with its attempt count raised, and the second failure in a row sends it to REVIEW (DIP-0009 v2.0). CANCELLED is item.dismiss; DEFERRED keeps the task with a wake date.",
        ],
    },
    {
        "slug": "morning",
        "group": "Work",
        "title": "The morning, on Winston",
        "lede": "Winston's scheduled morning, in UTC. The briefing waits for that morning's sleep reading before it is written.",
        "source": "box crontab: cos_news.sh 03:00 · triage-email.sh 03:30 · cos_inbox.sh 05:00 · cos_research.sh 05:30 · "
                  "cadence_run.py cadence-firm-cos-briefing 06:15 → cos_morning.sh · catch-up 06:45–10:45 · cos_verify_morning.sh 08:45",
        "lanes": ["WINSTON · BOX", "YOU"],
        "steps": [
            ("news", 0, 0, "03:00 News", "cos_news.sh", "", "", ""),
            ("mail", 0, 1, "03:30 Email triage", "triage-email.sh", "", "", ""),
            ("inbox", 0, 2, "05:00 Inbox run", "cos_inbox.sh", "", "", ""),
            ("res", 0, 3, "05:30 Research", "cos_research.sh", "", "", ""),
            ("brief", 0, 4, "06:15 Morning briefing", "cadence → cos_morning.sh", "", "focal", "Sr"),
            ("read", 1, 5, "Briefing, audio, opener", "## Daily Briefing", "", "", ""),
            ("ver", 0, 6, "08:45 Verify the morning", "cos_verify_morning.sh", "", "", ""),
        ],
        "edges": [("news", "mail", ""), ("mail", "inbox", ""), ("inbox", "res", ""), ("res", "brief", ""),
                  ("brief", "read", "JOURNAL"), ("read", "ver", "CHECKED")],
        "notes": [
            "The briefing is a cadence run (see 'A venture cadence run'): signed start and end events around it.",
            "The briefing holds until 08:15 Ljubljana for the sleep reading; a catch-up job reruns a missed morning hourly from 06:45 to 10:45.",
            "One run, two checks: the briefing job (box-briefing) and the briefing cadence (cadence-firm-cos-briefing) both judge the same journal page, so one fault opens two repair tasks.",
        ],
    },
    {
        "slug": "ledger-finality",
        "group": "Ledger",
        "title": "How an event becomes final: admit, fold, seal",
        "lede": ("Every change is an event appended to its writer's own log. The hub on Winston "
                 "admits or refuses it at once; every machine folds the same events to the same "
                 "state; a seal names exactly which events are settled."),
        "source": "datacored api/ledger_hub.py (seal scheduler, 900 s) · ledger/log.py EventLog.append · ledger/hub.py HubWriter, Hub.append, Hub.seal · ledger/hub_admission.py check_event · "
                  "ledger/fold.py fold · ledger/seal.py · ledger_seal.py status · <space>/.datacore/ledger-hub.json",
        "lanes": ["WRITER · ANY MACHINE", "HUB · WINSTON", "THE SPACE'S GIT REPO", "EVERY READER"],
        "steps": [
            ("wr", 0, 0, "Event written locally", "events/<actor>.jsonl", "", "", "S"),
            ("sub", 0, 1, "Sent to the hub", "HubWriter · buffers if down", "", "", ""),
            ("adm", 1, 2, "Admission check", "signed · chained · allowed", "", "focal", ""),
            ("ans", 1, 3, "Accepted or refused", "an event in the hub's log", "", "", "S"),
            ("pub", 2, 4, "Published by git", "ledger cycle, :25 hourly", "", "", "r"),
            ("fold", 3, 5, "Fold to state", "fold.py · pure, HLC order", "", "", ""),
            ("seal", 1, 6, "Seal", "watermarks → state root", "", "focal", "S"),
            ("set", 3, 7, "Settled", "ledger_seal.py status", "", "", ""),
        ],
        "edges": [("wr", "sub", ""), ("sub", "adm", "SUBMIT"), ("adm", "ans", ""), ("ans", "pub", "OWN LOG"),
                  ("ans", "seal", "ADMITTED ONLY"), ("pub", "fold", "PULL"), ("fold", "set", ""), ("seal", "set", "RECOMPUTE")],
        "notes": [
            "The hub is on in all ten spaces: Winston grants and seals, the Mac is the owner (each space's ledger-hub.json). The owner's hub.enable and every policy change (policy.set) are owner-signed records in the ledger.",
            "Admission is one rule for every path an event takes, HTTP or a git copy or a rebuild: signed by its writer with a known key, chained onto its log, a sane clock, and an allowed transition (assignee, approver, co-sign, lease). The first refused event ends what is admitted of that log.",
            "The fold is a pure function: the same events give the same state on every machine. When two agents race for one task, the earliest HLC wins and the later claim is recorded as a no-op.",
            "A seal names each writer's highest included event and the folded state root, so anyone can recompute it offline. Only the sequencer's (Winston's) seals count; another writer's seal is ignored. Sealed means final.",
            "The hub is datacored, the Datacore app's daemon, running in Winston's pod on the box. It seals every hub space it serves every 15 minutes (DATACORE_HUB_SEAL_INTERVAL_S, default 900 s; checked 2026-10-31: the latest 2-datacore seal is Winston's and verifies). The hub process still has no job contract, so no checker watches that it keeps running (a gap).",
        ],
    },
    {
        "slug": "ledger-corrections",
        "group": "Ledger",
        "title": "Correcting a wrong event",
        "lede": ("History is never rewritten. Inside the correction window an event can be reversed; "
                 "after its seal, or after one hour, only a new forward event can correct it."),
        "source": "ledger/finality.py (one rule for every reader) · ledger/voids.py REVERSAL_MAX_AGE_MS = 1 h · ledger.void · ledger.compensate · "
                  "ledger.admit / ledger.reject · owner decisions 2026-10-23",
        "lanes": ["YOU · INSIDE THE WINDOW", "HUB · WINSTON", "YOU · AFTER THE SEAL", "EVERY READER"],
        "steps": [
            ("bad", 0, 0, "A wrong event is found", "a task, spend or owner", "", "", ""),
            ("win", 1, 1, "Sealed, or over 1 hour?", "hub's admission clock", "", "focal", ""),
            ("void", 0, 2, "Reverse it", "ledger.void", "", "", "O"),
            ("comp", 2, 2, "Forward correction", "ledger.compensate", "", "", "O"),
            ("adm", 1, 3, "Hub admits it, once", "ledger.admit", "", "", "S"),
            ("inc", 2, 3, "Incident recorded", "names the missing gate", "", "", ""),
            ("eff", 3, 4, "Takes effect everywhere", "finality.py · one rule", "", "", ""),
        ],
        "edges": [("bad", "win", ""), ("win", "void", "NO"), ("win", "comp", "YES"), ("void", "adm", ""),
                  ("comp", "adm", ""), ("comp", "inc", ""), ("adm", "eff", "")],
        "notes": [
            "Who may reverse is the space owner named in the owner's own ledger records, never the local principal file. The window runs on the hub's clock (when it admitted the event), never on a time the writer chose.",
            "A reversal outside the window, or of a sealed event, gets ledger.reject and has no effect. A sealed outcome never changes meaning; the original stays confirmed and the correction points to it.",
            "Control records are never reversible: admits, rejects, seals, hub.enable, key changes, approvals and grants.",
            "A malformed event (a hash that never matched its body) is voided by a human or a named voider, never by the event's own writer (voids.py).",
        ],
    },
    {
        "slug": "ledger-daily",
        "group": "Ledger",
        "title": "Every day the ledger checks itself",
        "lede": ("Once a day Winston verifies every space's ledger, checks the lists against it, writes "
                 "a checkpoint and proves the checkpoint restores."),
        "source": "ledger_daily.sh (box 05:35 UTC) · ledger_cli.py verify --full · shadow_check.py · ledger_checkpoint.py write / verify · "
                  "two box job contracts read ledger-verify.log and shadow-status.json",
        "lanes": ["WINSTON · BOX", "A THROWAWAY SPACE", "YOU"],
        "steps": [
            ("ver", 0, 0, "05:35 Verify every space", "ledger_cli.py verify --full", "", "focal", ""),
            ("drift", 0, 1, "Lists match the ledger?", "shadow_check.py", "", "", ""),
            ("ckw", 0, 2, "Write a checkpoint", "ledger_checkpoint write", "", "", ""),
            ("ckr", 1, 3, "Restore it and compare", "checkpoint verify", "", "", ""),
            ("log", 0, 4, "Results for the checker", "ledger-verify.log", "", "", ""),
            ("you", 2, 5, "One alert if it fails", "job_verify · Telegram", "", "", ""),
        ],
        "edges": [("ver", "drift", ""), ("drift", "ckw", ""), ("ckw", "ckr", "IMPORT"), ("ckr", "log", "SAME ITEMS?"),
                  ("log", "you", "FAIL")],
        "notes": [
            "Verify checks each log's hash chain, sequence and signatures. A space that cannot be checked on this machine reads 'could not check', never a pass.",
            "The checkpoint matters because next_actions.org is now generated from the ledger, so it is no longer a fallback. A checkpoint nobody restored is a claim; this one is restored daily.",
            "Checkpoints live in .datacore/checkpoints/, never in org/: a copy beside the authored file once made every id appear twice and cost 1,204 rewritten ids (2026-08-12).",
        ],
    },
    {
        "slug": "sync-cycle",
        "group": "Ledger",
        "title": "How work moves between machines",
        "lede": ("Every machine keeps its own copy of each space. Writers never share a file: each appends "
                 "to its own event log, so merging is a union and cannot conflict."),
        "source": "ledger_phase1_cycle.sh (:25 hourly on box, overnight host, dayshift) · ledger_transport.py sync (DIP-0046) · ledger/fold.py · "
                  "provenance commit_hook.py (where installed)",
        "lanes": ["THIS MACHINE", "THE SPACE'S GIT REPO", "EVERY OTHER MACHINE"],
        "steps": [
            ("edit", 0, 0, "Work is done", "events/<actor>.jsonl", "", "", "S"),
            ("cyc", 0, 1, "Hourly ledger cycle, :25", "ledger_phase1_cycle.sh", "", "", ""),
            ("push", 0, 2, "Commit, merge, push", "ledger_transport.py sync", "", "", "r"),
            ("repo", 1, 3, "One log per writer", "events/*.jsonl", "", "focal", ""),
            ("pull", 2, 4, "Pull and merge", "union, never rebase", "", "", ""),
            ("proj", 2, 5, "Fold and project", "next_actions.org", "", "", ""),
        ],
        "edges": [("edit", "cyc", ""), ("cyc", "push", ""), ("push", "repo", "PUSH"),
                  ("repo", "pull", "PULL"), ("pull", "proj", "")],
        "notes": [
            "A sync ends clean, offline (retries by itself), blocked (login or remote gone: tell the owner) or conflict (markers left for a person). Nothing is stashed or reset to make it pass.",
            "Two writers on one shared file breaks this rule: that is what the duplicate delegation review did with its receipt.",
            "Where the provenance git hook is installed, each commit is queued and anchored in one signed commit-batch receipt per drain: commit and tree hashes only, no message, author or path.",
        ],
    },
    {
        "slug": "scheduled-job",
        "group": "Schedules",
        "title": "A scheduled job: declared, installed, run, checked",
        "lede": ("Every scheduled job is declared once, with its machine, its time and the evidence it must "
                 "leave. Each machine installs its own jobs and checks them every hour."),
        "source": "jobs/manifest.yaml (+ manifest.local.yaml) · cron_install.py (# datacore-job: lines) · jobs/run.py · jobs/checks.py · "
                  "job_verify.py --machine <m> (hourly) · jobs/autofix.py",
        "lanes": ["THE MANIFEST · GIT", "THE MACHINE'S SCHEDULER", "THE JOB", "THE MACHINE'S CHECKER"],
        "steps": [
            ("dec", 0, 0, "Declared once", "jobs/manifest.yaml", "", "", ""),
            ("ins", 1, 1, "Installed on its machine", "cron_install.py", "", "", ""),
            ("fire", 1, 2, "Fires on schedule", "cron · launchd · systemd", "", "", ""),
            ("env", 2, 3, "Runs in one envelope", "jobs/run.py", "", "", ""),
            ("art", 2, 4, "Leaves its evidence", "the declared artifacts", "", "focal", ""),
            ("chk", 3, 5, "Hourly contract check", "job_verify.py", "", "", "S"),
            ("rep", 3, 6, "Third failure: a repair", "see the repair loop", "", "", ""),
        ],
        "edges": [("dec", "ins", "MACHINE: X"), ("ins", "fire", ""), ("fire", "env", ""), ("env", "art", "EXIT 0 + FILE"),
                  ("art", "chk", "READ"), ("chk", "rep", "FAIL ×3")],
        "notes": [
            "The envelope gives cron, launchd and a person at a prompt the same environment, and a job that exits 0 but writes nothing fails: no success without evidence.",
            "The installer touches only lines marked '# datacore-job:', keeps a private backup and verifies what it wrote; unrelated crontab lines stay as they were.",
            "The checker records a signed verdict per job (metric.attest); the delegation and health views read those verdicts (job_attestations.py). 33 scheduled scripts have no contract yet: see Duplicates and overlaps.",
        ],
    },
    {
        "slug": "cadence-run",
        "group": "Schedules",
        "title": "A venture cadence run",
        "lede": ("A cadence is an agent's recurring duty, declared in a venture.yaml. One script runs it "
                 "the same way on every host and judges it by the evidence it leaves, never by the agent's word."),
        "source": "venture.yaml cadences · ventures/lib/cadence_schedule_sync.py · ventures/lib/cadence_run.py (DIP-0050) · "
                  "cadence-control.yaml (pause) · principals.yaml verify_keys · cadence_liveness.py (box 07:40)",
        "lanes": ["THE PLAN · VENTURE.YAML", "THIS HOST · CADENCE_RUN.PY", "THE AGENT", "WINSTON · CHECKS"],
        "steps": [
            ("plan", 0, 0, "Cadence declared", "owner, time, evidence", "", "", ""),
            ("sync", 1, 1, "Scheduler synced", "cadence_schedule_sync", "", "", ""),
            ("gate", 1, 2, "Refusals checked", "owner · pause · budget · key", "", "", ""),
            ("start", 1, 3, "Start recorded", "metric.attest · start", "", "", "S"),
            ("run", 2, 4, "The agent does the work", "template + evidence spec", "", "", "r"),
            ("judge", 1, 5, "Judged by its evidence", "never by its word", "", "focal", ""),
            ("end", 1, 6, "End recorded", "sha256 of the artifact", "", "", "S"),
            ("box", 3, 7, "Re-verified on the box", "cadence_liveness 07:40", "", "", ""),
        ],
        "edges": [("plan", "sync", "INSTALL"), ("sync", "gate", "FIRES"), ("gate", "start", "ALLOWED"),
                  ("start", "run", ""), ("run", "judge", "ARTIFACT"), ("judge", "end", ""), ("end", "box", "GIT")],
        "notes": [
            "The run refuses unless this host's actor owns the cadence, the fleet is not paused for it, the daily ceiling has room, it has not failed three times in a row, and the actor signs with its proven key. Signing here is not optional.",
            "The schedule sync never touches a job that is not a cadence, snapshots before every write, and refuses to remove more than a quarter of the cadence jobs without a committed migration file.",
            "The box re-checks the signed end event and that the artifact at that sha256 is in git. cadence_liveness counts overdue cadences into a file a job contract reads: before it, the PR-review cadence had stopped silently for 136 days.",
        ],
    },
    {
        "slug": "repair-loop",
        "group": "Schedules",
        "title": "When a job fails: the repair loop",
        "lede": ("Each machine checks its own jobs. A job that fails three times in a row becomes a "
                 "repair task for that machine's own agent; only a repair the agent gives up on reaches you."),
        "source": "job_verify.py (hourly, every machine) · jobs/autofix.py delegate/escalations · fix_check.py · "
                  "box-autofix-escalation (:25 hourly on Winston)",
        "lanes": ["THE MACHINE'S CHECKER", "THE MACHINE'S AGENT", "WINSTON · ESCALATION", "YOU"],
        "steps": [
            ("chk", 0, 0, "Hourly job check", "job_verify.py", "", "", "S"),
            ("third", 0, 1, "Third failure in a row", "same fingerprint", "", "", ""),
            ("task", 1, 2, "Repair task", "autofix.delegate", "", "", "S"),
            ("fix", 1, 3, "Fix in a clean copy", "fix_check.py", "", "", "r"),
            ("pass", 0, 4, "Job passes again", "repair closed", "", "focal", ""),
            ("gave", 2, 5, "Gave up after 3 tries", "escalations, :25", "", "", ""),
            ("you", 3, 6, "One alert, once", "Telegram · ack", "", "", ""),
        ],
        "edges": [("chk", "third", ""), ("third", "task", "DELEGATE"), ("task", "fix", ""),
                  ("fix", "pass", "CHECK PASSES"), ("fix", "gave", "3 FAILED"), ("gave", "you", "ALERT")],
        "notes": [
            "The agent is the one whose machine the job runs on (principals.yaml hosts): Winston for the box, Miles for the overnight host. A job on your Mac becomes a task for you, never an agent repair.",
            "A give-up stops alerting when its job passes again or 48 hours after it was closed.",
        ],
    },
]


# -- the second batch (owner, 2026-10-31: "do all 16, group them by importance or what they do"),
# each drawn from the code by a research pass and checked for crossings before merging.
FLOWS += [{'slug': 'overnight-run',
  'group': 'Delegation and overnight',
  'title': 'Overnight execution on Miles',
  'lede': 'Miles works through the queue of AI tasks on a timer. A space with a git problem sits out instead of stopping the night, and every result '
          "lands in its space's 0-inbox for your review.",
  'source': 'nightshift-preflight (:45 every 3 h, nightshift run --preflight) · nightshift-overnight (nightshift run, systemd timer) · '
            'nightshift/lib/run.py run_task_mode · task_queue.py build_queue, delegation_status · claim.py claim_task, complete_task · '
            'ledger_hooks.py lifecycle · evaluate.py make_decision · output.py write_output',
  'lanes': ['MILES · THE RUN', 'THE TASK', 'THE SPACE · YOU', 'HELD OR FAILED'],
  'steps': [('pf', 0, 0, 'Preflight, every 3 hours', 'same check, ahead of time', '', '', ''),
            ('fire', 1, 0, "The night's run starts", 'nightshift run · timer', '', '', ''),
            ('git', 0, 1, 'Git check and pull', 'a bad space sits out', '', 'focal', ''),
            ('q', 0, 2, 'The queue is chosen', 'nightshift.org · priority', '', '', ''),
            ('adm', 1, 3, 'Each task admitted?', 'approval · budget · inbox', '', 'focal', ''),
            ('claim', 1, 4, 'Claim the task', 'item.claim · clock.start', '', '', 'S'),
            ('held', 3, 4, 'Held, not run', "awaits Winston's approval", '', '', ''),
            ('exe', 1, 5, 'The agent does the work', 'execute.py · agent by tag', '', '', 'r'),
            ('ev', 1, 6, 'Evaluators score it', 'consensus vs threshold', '', '', ''),
            ('fail', 3, 6, 'Failed: one retry', 'back to NEXT · attempts+1', 'NEXT', '', ''),
            ('out', 2, 7, 'Result in 0-inbox', '<space>/0-inbox/ report', '', '', ''),
            ('esc', 3, 7, 'Second failure in a row', 'escalated with last error', '', '', ''),
            ('rev', 2, 8, 'Waits for your review', 'item.complete', 'REVIEW', '', 'S')],
  'edges': [('pf', 'git', ''),
            ('fire', 'git', ''),
            ('git', 'q', ''),
            ('q', 'adm', ''),
            ('adm', 'claim', 'ADMITTED'),
            ('adm', 'held', 'NOT APPROVED'),
            ('claim', 'exe', ''),
            ('exe', 'ev', 'OUTPUT'),
            ('exe', 'fail', 'FAILED'),
            ('ev', 'out', 'WRITE'),
            ('fail', 'esc', 'AGAIN'),
            ('out', 'rev', ''),
            ('esc', 'rev', 'TO REVIEW')],
  'notes': ['The preflight job runs the same git check the run opens with, every three hours, so a space that would sit out tonight is found and '
            "sent for repair before the night (see 'When a job fails: the repair loop'). Until 2026-09-21 one stray file in one space refused the "
            'whole run; now only that space sits out.',
            "Admission is one rule for every task: a task an agent made (cadence, follow-up, agent) runs only once Winston's delegation review has "
            "approved it; work outside your stated priorities, a task whose inbox cannot be resolved, or a paid call over the day's budget is turned "
            "away before the claim. See 'The delegation safety loop'.",
            "An approved result would normally go to DONE, but in a space whose task list comes from the ledger the worker's report is not your "
            "acceptance, so it goes to REVIEW. A low-consensus result also goes to REVIEW and is sent to Winston's approval queue.",
            'A failure never closes a task: it goes back to NEXT with its attempt count raised, and the second failure in a row sends it to REVIEW '
            'naming the last error. A quota or budget wall is a deferral, not a failure.',
            'Not verified: how often the run fires. The manifest says hourly (owner, 2026-10-02), but the timer file in the module still says 20:00 '
            'and 06:00 and runs as user deploy; the live timer on the machine was not read.']},
 {'slug': 'delegation-safety',
  'group': 'Delegation and overnight',
  'title': 'The delegation safety loop',
  'lede': "One step decides whether agent-made work may run: Winston's nightly review and the receipt it commits. Every other check here proves the "
          'loop still works, and a failure is an alert to you, not a stop.',
  'source': 'box-delegation-review 23:30 (chief-of-staff lib.cli review, delegation_gate.py) · delegation_receipt.py fresh_hours · task_queue.py '
            'delegation_status (25 h) · nightshift-actor-presence :25 hourly (actor_presence.py) · box-delegation-round Mon 06:00 '
            '(delegation_fleet_exercise.py start) · nightshift-delegation-drill 03:30 (delegation_drill.py) · box-delegation-canary 04:15 '
            '(delegation_canary.py --run) · nightshift-delegation-gate 08:40 (gate_check.py)',
  'lanes': ['WINSTON · BOX', 'SHARED · 2-DATACORE', 'MILES · OVERNIGHT', 'YOU'],
  'steps': [('rev', 0, 0, '23:30 Delegation review', 'approves · APPROVED_BY', '', 'focal', ''),
            ('pres', 2, 0, 'Hourly: every log there', 'actor_presence.py · :25', '', '', ''),
            ('rcpt', 1, 1, 'Review receipt', 'cos-lastrun = lastattempt', '', 'focal', ''),
            ('alp', 3, 1, 'Alert: a log is missing', 'stays red until fixed', '', '', ''),
            ('adm', 2, 2, 'Run admits the task?', 'approved · receipt ≤ 25 h', '', '', ''),
            ('round', 0, 3, 'Mon 06:00 Weekly round', 'every pair hands one task', '', '', 'S'),
            ('hold', 1, 3, 'Held, not run', 'waits for the next review', '', '', ''),
            ('run', 2, 3, 'The task runs', 'see Overnight execution', '', '', ''),
            ('alr', 3, 4, 'Alert: round not started', 'contract · Telegram', '', '', ''),
            ('drill', 2, 4, '03:30 Delegation drill', '27 scenarios · offline', '', '', ''),
            ('can', 0, 5, '04:15 Canary for Miles', 'item.create · 20 h budget', '', '', 'S'),
            ('ald', 3, 5, 'Alert: a control failed', 'never sent for repair', '', '', ''),
            ('ccl', 2, 6, 'Miles closes the canary', 'claim · run · check', '', '', 'S'),
            ('verd', 0, 7, 'Next 04:15 judges it', 'delegation-canary.json', '', '', ''),
            ('gate', 2, 7, '08:40 Gate check', 'no unapproved full run', '', '', ''),
            ('al', 3, 8, 'One alert to you', 'delegation keeps running', '', '', '')],
  'edges': [('rev', 'rcpt', 'COMMIT'),
            ('pres', 'alp', 'MISSING'),
            ('rcpt', 'adm', 'GIT PULL'),
            ('adm', 'hold', 'NO'),
            ('adm', 'run', 'YES'),
            ('round', 'alr', 'FAILED'),
            ('drill', 'ald', 'FAILED'),
            ('can', 'ccl', 'ITEM'),
            ('ccl', 'verd', 'COMPLETED?'),
            ('verd', 'al', 'FAILED'),
            ('gate', 'al', 'VIOLATION')],
  'notes': ['The review approves agent-made tasks that score high enough on your priorities (it stamps APPROVED_BY) and parks the rest as DEFERRED. '
            'It writes two files, an attempt and a receipt; Miles reads them only when both say complete and name the same review, writer and time. '
            "Until 2026-10-30 Miles ran the same review at 05:55, so two writers shared those files and the merge read as 'hold'.",
            'Without an approval a task an agent made never runs, not even as a proposal. Tasks you created run without the review.',
            'The canary, drill, round, presence and gate checks only alert. None of them pauses delegation, and the canary, drill and round are '
            'never handed to an agent for repair, because they check the agent that would repair them (that loop happened on 2026-09-22).',
            'The drill runs 27 delegation mishaps in a scratch copy with no model and no network; its contract pins the count at 27, so a drill that '
            "silently lost scenarios fails. The canary judges yesterday's item before it seeds a new one, so an item that never closed is written "
            'down as failed rather than replaced.',
            'Not verified: whether a missing or stale receipt holds approved tasks on Miles. That check runs only when the review-before-execution '
            "setting is on in Miles's environment, and it is off unless set; with it off, the APPROVED_BY stamp alone lets a task run. The "
            'environment file was not read.']},
 {'slug': 'evening-handoff',
  'group': 'Delegation and overnight',
  'title': 'The evening hand-off, Winston to Miles',
  'lede': "At 20:00 Winston runs the evening review, queues the night's AI work and pushes it. The 23:30 delegation review approves what agents "
          "created, and Miles's next run pulls it all and starts.",
  'source': "box-tomorrow 20:00 (cos_tomorrow.sh → cos_llm_call '/tomorrow') · commands/tomorrow.md step 8 (nightshift.org) · cos_questions.py "
            'submit / decisions · winston_send.py · cos_sync.sh · box-delegation-review 23:30 · nightshift-overnight (git pull, build_queue)',
  'lanes': ['WINSTON · BOX', 'THE SPACES · GIT', 'MILES · OVERNIGHT', 'YOU'],
  'steps': [('sync', 0, 0, '20:00 Sync first', 'cos_sync.sh', '', '', ''),
            ('tom', 0, 1, 'Evening review runs', '/tomorrow · cos_llm_call', '', '', ''),
            ('queue', 0, 2, 'AI tasks queued', '→ nightshift.org', 'NEXT', '', ''),
            ('tg', 3, 2, 'Review on Telegram', 'questions: Approve/Dismiss', '', '', ''),
            ('push', 1, 3, 'Committed and pushed', 'git commit · cos_sync.sh', '', '', ''),
            ('rev', 0, 4, '23:30 Delegation review', 'approves agent-made tasks', '', 'focal', ''),
            ('rcpt', 1, 5, 'Approvals and receipt', 'APPROVED_BY · cos-lastrun', '', '', ''),
            ('pick', 2, 6, "Miles's next run", 'git pull · build_queue', '', 'focal', ''),
            ('run', 2, 7, "The night's work runs", 'see Overnight execution', '', '', ''),
            ('morn', 3, 8, 'Results for the morning', '0-inbox · your review', 'REVIEW', '', '')],
  'edges': [('sync', 'tom', ''),
            ('tom', 'queue', ''),
            ('tom', 'tg', 'SUMMARY'),
            ('queue', 'push', 'PUSH'),
            ('push', 'rev', ''),
            ('rev', 'rcpt', 'COMMIT'),
            ('rcpt', 'pick', 'PULL'),
            ('pick', 'run', ''),
            ('run', 'morn', 'OUTPUT')],
  'notes': ["The evening review is the /tomorrow command run by Winston's model without a person at the prompt. Its questions become Approve/Dismiss "
            "items on Telegram, and your answers from the last three days are fed into the next evening's run, which is how a decision reaches the "
            'queue.',
            "Queueing means a task tagged AI is placed in nightshift.org. That is the only queue Miles's overnight run reads; a task queued there is "
            "not offered to the other agents' claim loops (see 'A task's lifecycle').",
            "Tasks you created run on Miles's next run. Tasks an agent created wait for the 23:30 delegation review (see 'The delegation safety "
            "loop').",
            'Not verified: whether the evening review actually moves tasks into nightshift.org on an unattended run (the command asks for '
            'confirmation), and whether it marks tasks it creates as agent-made. Neither could be confirmed from code.',
            "Not verified: the evening review's ledger record. cos_tomorrow.sh calls cos_ledger_event.sh, which does not exist in this repository, "
            'so that call may do nothing.']},
 {'slug': 'deploy-config',
  'group': 'Fleet operations',
  'title': 'Deploy and configuration: pushed, pulled, kept true',
  'lede': 'You run production deploys, and a deploy only ships what is committed and pushed: Winston pulls it, never receives a copy. Scheduled '
          'checks on Winston and on your Mac then say when a machine has drifted.',
  'source': 'chief-of-staff server/deploy.sh (run by you) · runner-refresh :17 hourly · box-reconcile-timers :20 hourly (ops_reconcile_timers.sh) · '
            'box-setup-verify 07:10 (cos-server-setup.sh --verify) · box-deploy-drift 07:25 (cos_deploy_drift.sh) · box-fleet-sync 06:10 and 18:10 '
            '(git_fleet_sync.py --execute --pull) · mac-config-drift and mac-config-resolution on every converged join (visitor_join.py)',
  'lanes': ['YOU · MAC', 'WINSTON · THE DEPLOY', 'WINSTON · SCHEDULED', 'YOUR MAC · ON JOIN'],
  'steps': [('push', 0, 0, 'You commit and push', 'the module repo', '', '', ''),
            ('gate', 0, 1, 'Refused unless pushed', 'deploy.sh · clean, on origin', '', '', ''),
            ('fs', 2, 0, '06:10, 18:10 fleet sync', 'git_fleet_sync.py', '', '', ''),
            ('fs2', 2, 1, 'Trapped work pushed', 'never a gated branch', '', '', ''),
            ('join', 3, 0, 'Your Mac rejoins', 'visitor_join.py', '', '', ''),
            ('cd', 3, 1, 'Hooks on every machine', 'config_drift.py', '', '', ''),
            ('cr', 3, 2, 'This Mac finds its config', 'config_resolution_probe', '', '', ''),
            ('pull', 1, 2, 'Pulled, never copied', 'git pull --ff-only', '', 'focal', ''),
            ('link', 1, 3, 'Scripts linked to clone', 'a differing file: refused', '', '', ''),
            ('rr', 2, 4, ':17 hourly core pull', 'runner-refresh · ff-only', '', '', ''),
            ('rec', 2, 5, ':20 one timer repaired', 'ops_reconcile_timers.sh', '', '', ''),
            ('ver', 2, 6, '07:10 setup verified', '--verify · changes nothing', '', '', ''),
            ('drift', 2, 7, '07:25 deployed vs source', 'cos_deploy_drift.sh', '', 'focal', ''),
            ('you', 0, 8, 'A difference: you decide', 'which side goes back to git', '', '', '')],
  'edges': [('push', 'gate', ''),
            ('gate', 'pull', 'DEPLOY'),
            ('pull', 'link', ''),
            ('fs', 'fs2', ''),
            ('join', 'cd', ''),
            ('cd', 'cr', ''),
            ('link', 'rr', 'THEN KEPT TRUE'),
            ('rr', 'rec', ''),
            ('rec', 'ver', ''),
            ('ver', 'drift', ''),
            ('drift', 'you', 'DIFFERS')],
  'notes': ['The deploy refuses a module repo with uncommitted or unpushed changes, and on Winston it refuses a script that differs from the clone: '
            'a fix made only on the machine is left untouched and reported, never stashed, reset or overwritten. --force overrides nothing.',
            'The drift check names files that differ but never says which side is newer: a hash cannot tell, and guessing is how a fix made on the '
            'box gets overwritten.',
            'The timer repair is an allow-list of one unit (the login-token refresh timer); any other drift stays a human decision. The fleet sync '
            'never touches a feature branch or a branch with a review gate, and refuses to push a deletion or a ledger fork.',
            "The two Mac checks run after every converged join: the hook check reaches the other machines over the Mac's own access, and a machine "
            'it cannot reach is reported, never counted as fine.',
            'Not verified: that each of these jobs ran recently; this drawing is from the code and the schedules, not from their logs.']},
 {'slug': 'backups',
  'group': 'Fleet operations',
  'title': 'Backups, and proof that they restore',
  'lede': 'Org files and journals already live in git. Winston backs up what git does not hold, PLUR memory and runtime state, every night, and once '
          'a week proves the newest copy restores. Your Mac pushes its knowledge indexes to Miles monthly.',
  'source': 'box-backup 02:30 (cos_backup.sh) · box-restore-check Sunday 03:10 (cos_restore_check.sh) · mac-knowledge-backup and '
            'mac-knowledge-personal-backup, first arrival each month (sqlite_push.py) · jobs/manifest.yaml',
  'lanes': ['WINSTON · NIGHTLY', 'OFF THE BOX', 'WINSTON · WEEKLY PROOF', 'YOUR MAC · MONTHLY'],
  'steps': [('snap', 0, 0, '02:30 snapshot', 'cos_backup.sh · keep 14', '', '', ''),
            ('off', 1, 1, 'Offsite copy, keep 7', 'storage machine, one folder', '', '', ''),
            ('rc', 2, 2, 'Sunday 03:10 restore check', 'cos_restore_check.sh', '', 'focal', ''),
            ('cnt', 2, 3, 'Opened and counted', 'engrams within 5% of live', '', '', ''),
            ('res', 2, 4, 'Pass, or an alert to you', 'cos_alert.sh · Telegram', '', '', ''),
            ('arr', 3, 0, 'First arrival of a month', 'visitor_join · every: month', '', '', ''),
            ('cons', 3, 1, 'Consistent snapshot', 'sqlite backup API', '', '', ''),
            ('push', 3, 2, 'Pushed to Miles', 'temp name, then rename', '', '', ''),
            ('size', 3, 3, 'Size read back', 'sqlite_push: ok', '', '', '')],
  'edges': [('snap', 'off', 'OFFSITE'),
            ('off', 'rc', 'SAME SIZE?'),
            ('rc', 'cnt', ''),
            ('cnt', 'res', ''),
            ('arr', 'cons', ''),
            ('cons', 'push', ''),
            ('push', 'size', '')],
  'notes': ["The nightly snapshot holds PLUR memory and runtime state; the memory store's own git history is left out because it lives on its sync "
            'remote and once filled the offsite disk.',
            'Retention offsite is enforced from Winston, and if the listing that decides what to keep fails, nothing is deleted.',
            'The weekly check asks what a restore would ask: does the newest snapshot extract, does the memory file parse with an engram count '
            'within 5% of the live store, did runtime state come along, and is the same file offsite at the same size. Only all four make it pass.',
            "Each Mac job copies one knowledge index (the shared one, and the personal space's) as of one read, so a copy is never taken mid-write.",
            "Not verified: there is no restore check for the Mac's knowledge-index copies on Miles; the only proof is the size read back after the "
            'push.']},
 {'slug': 'credentials',
  'group': 'Fleet operations',
  'title': 'Credentials: asked for, verified, never searched',
  'lede': 'A program that needs a credential asks the broker for it by name. The broker resolves one declared place, checks the value with its '
          'provider, and refuses one it can prove dead. Rotation is always yours.',
  'source': 'creds broker: get, compare, doctor, sync · mac-creds-sync and mac-creds-distribute on the first converged join of a session · '
            'hermes-creds-sync hourly on Tris · CLAUDE.md Credentials section',
  'lanes': ['YOUR MAC · THE STORE', 'EVERY MACHINE', 'A PROGRAM · CREDS GET', 'YOU'],
  'steps': [('arr', 0, 0, 'A session arrives', 'first converged join', '', '', ''),
            ('sync', 0, 1, 'Store synced here', 'edits committed, pushed', '', '', ''),
            ('dist', 0, 2, 'Assembled for each host', 'mac-creds-distribute', '', '', ''),
            ('hosts', 1, 3, 'Each host holds its copy', 'Tris re-syncs hourly', '', '', ''),
            ('get', 2, 4, 'Asked for by name', 'creds get · consumer named', '', '', ''),
            ('loc', 2, 5, 'One declared place', "this host's value first", '', '', ''),
            ('chk', 2, 6, 'Checked with provider', 'ok · n-a · FAIL', '', 'focal', ''),
            ('srv', 2, 7, 'Served to the program', 'n-a is served, flagged', '', '', ''),
            ('cmp', 1, 7, 'Compared on every host', 'fingerprints, never values', '', '', ''),
            ('rot', 3, 8, 'Rotation is yours', 'agents flag, never rotate', '', 'focal', '')],
  'edges': [('arr', 'sync', ''),
            ('sync', 'dist', ''),
            ('dist', 'hosts', 'DISTRIBUTED'),
            ('hosts', 'get', 'WHEN NEEDED'),
            ('get', 'loc', ''),
            ('loc', 'chk', ''),
            ('chk', 'srv', 'OK / N-A'),
            ('chk', 'cmp', 'FAIL'),
            ('cmp', 'rot', 'DEAD EVERYWHERE')],
  'notes': ['Nothing searches for a credential. A found value says nothing about whether it is current, which is how duplicates pile up and how a '
            'working credential gets reported missing.',
            'The broker holds a lock per credential for the whole request, because a rotating refresh token works only once. A value proven dead '
            "(FAIL) is never served; 'n-a' means it could not tell, is served with a warning, and never counts as a pass.",
            "A FAIL is one host's copy. Before anyone calls a credential revoked, its fingerprint is compared on every host it lives on; until then "
            "the answer is 'cannot tell yet', never 'rotate it'.",
            'A changed value goes into the store and is then synced; the assembled copy on a machine is generated and an edit to it is lost on the '
            'next sync.',
            'Not verified: how the distribute step reaches each machine, and whether Winston, Miles and Data re-sync on their own schedule; only '
            "Tris's hourly sync is in the job list."]},
 {'slug': 'owner-decisions',
  'group': 'You',
  'title': 'Your decisions: board, save, summary, apply',
  'lede': "You decide on a local page, not in chat. Claude files what you saved, tells you what you chose, and changes nothing until you say 'apply "
          "the decisions'.",
  'source': 'mac-review-board daily after 05:00 on arrival (review_board.py) · decision-board skill · decision_board_collect.py · '
            'gtd_decision_board.py apply · fleet_dashboard.py (saved answers, CREW_HOLD_DAYS = 3) · mac-pending-decisions on every join '
            '(detectors/pending_decisions.py)',
  'lanes': ['YOUR MAC · JOBS', 'YOU · THE PAGE', 'CLAUDE · THE SESSION', 'THE WORK · ORG, LEDGER'],
  'steps': [('rb', 0, 0, 'After 05:00: review board', 'review_board.py · REVIEW', '', '', ''),
            ('page', 1, 1, 'Board page, local only', 'owner-only, never published', '', '', ''),
            ('con', 0, 1, 'Fleet console', 'fleet_dashboard.py', '', '', ''),
            ('pick', 1, 2, 'You choose, add notes', 'Save · .decisions.json', '', 'focal', ''),
            ('crew', 0, 3, 'Crew rows leave 3 days', 'back if still failing', '', '', ''),
            ('file', 2, 3, 'Your answers filed', 'decision_board_collect.py', '', '', ''),
            ('sum', 2, 4, 'Summarised, nothing done', 'choices and notes read back', '', '', ''),
            ('say', 1, 5, 'You say: apply', 'in the session, explicitly', '', '', ''),
            ('app', 2, 6, 'Applied row by row', 'gtd_decision_board apply', '', 'focal', ''),
            ('pend', 0, 6, 'Withheld commits aged', 'pending_decisions.py', '', '', ''),
            ('led', 3, 7, 'Tasks change state', 'adapter · ledger events', '', '', 'S'),
            ('hand', 1, 7, 'Old ones: you resolve', 'commit or revert by hand', '', '', '')],
  'edges': [('rb', 'page', ''),
            ('page', 'pick', ''),
            ('con', 'pick', 'SAVED ANSWERS'),
            ('pick', 'crew', 'CONSOLE'),
            ('pick', 'file', 'BOARD'),
            ('file', 'sum', ''),
            ('sum', 'say', 'WAITS FOR YOU'),
            ('say', 'app', ''),
            ('app', 'led', ''),
            ('pend', 'hand', 'OVER 7 DAYS')],
  'notes': ['The review board lists every task in REVIEW with its evidence (PR or issue, report, test result, second-model review), each with '
            'Accept, Rework or Drop. Your own rules may pre-set some rows to Accept when tests are green and a reviewer from another model family '
            'passed the work; release, security, money, credentials and public posts always stay with you.',
            'Saving downloads a file; the collect step moves it into the owner-only boards folder, checks the copy reads back identical, and only '
            'then removes the download. Every answer is also kept in a private decisions dataset beside it.',
            'Apply checks that the board still matches the source files and the ledger, changes one row at a time and keeps a receipt; an '
            'interrupted row blocks a replay until it is reconciled. Anything irreversible is dry-run first.',
            'On the fleet console a row you hand to the crew (crew fix, retire, snooze) leaves for 3 days; if it still fails after that it comes '
            'back to you with the reason. A row you keep stays already chosen.',
            'The withheld-commit counter never blocks the overnight run; age is the signal. Not verified: whether the fleet console runs on a '
            'schedule or only when opened.']},
 {'slug': 'your-sessions',
  'group': 'You',
  'title': 'Your sessions: /today, /wrap-up, /handover',
  'lede': 'Three commands frame an interactive session on your Mac: the morning briefing, the close, and moving a live conversation to dayshift. '
          'Each leaves something behind that the next session reads.',
  'source': '.datacore/commands/today.md (steps 1–18, command_steps.py, briefing_state.py) · wrap-up.md · wrap_up_mechanics.py preflight / finalize '
            '/ audit / report · handover.md · handover.py checkpoint / go · provenance receipt_hook.py (Stop hook on the Mac) and post-commit hook',
  'lanes': ['/TODAY · MORNING', '/WRAP-UP · SESSION END', '/HANDOVER · TO DAYSHIFT', 'DAYSHIFT · RESUMED'],
  'steps': [('t1', 0, 0, 'Checklist in the journal', 'command_steps.py start', '', '', ''),
            ('t2', 0, 1, 'Real briefing already?', 'briefing_state.py', '', '', ''),
            ('t3', 0, 2, 'Gather the morning', 'vitals, calendar, mail, news', '', '', ''),
            ('t4', 0, 3, 'Briefing written', '## Daily Briefing, at top', '', 'focal', 'r'),
            ('t5', 0, 4, 'Standups, then post-hooks', 'steps 17 and 18', '', '', ''),
            ('t6', 0, 5, "Left: today's journal", 'briefing and ticked steps', '', '', ''),
            ('w1', 1, 0, 'Pulse asked, never waits', 'one line, keeps going', '', '', ''),
            ('w2', 1, 1, 'Preflight', 'transcript archived', '', '', ''),
            ('w3', 1, 2, 'Journals and tasks', 'inbox.org · continuations', '', '', ''),
            ('w4', 1, 3, 'Session files pushed', 'finalize · own files only', '', '', 'r'),
            ('w5', 1, 4, 'Audit, then the report', 'audit · report', '', '', ''),
            ('w6', 1, 5, 'Left: commits, archive', 'journal, learning queue', '', '', ''),
            ('h1', 2, 0, 'In-flight work stopped', 'relaunch list', '', '', ''),
            ('h2', 2, 1, 'Files recorded', 'files --add', '', '', ''),
            ('h3', 2, 2, 'Checkpoint, pushed', 'handover.py checkpoint', '', 'focal', ''),
            ('h4', 2, 3, 'You exit and run go', 'handover <session-id>', '', '', ''),
            ('d1', 3, 4, 'Repos fast-forwarded', 'all checked before any', '', '', ''),
            ('d2', 3, 5, 'Same chat resumes', 'tmux · same permissions', '', '', ''),
            ('d3', 3, 6, 'Left: checkpoint file', 'in-flight work relaunched', '', '', '')],
  'edges': [('t1', 't2', ''),
            ('t2', 't3', 'NONE OR STUB'),
            ('t3', 't4', ''),
            ('t4', 't5', ''),
            ('t5', 't6', ''),
            ('w1', 'w2', ''),
            ('w2', 'w3', ''),
            ('w3', 'w4', ''),
            ('w4', 'w5', ''),
            ('w5', 'w6', ''),
            ('h1', 'h2', ''),
            ('h2', 'h3', ''),
            ('h3', 'h4', ''),
            ('h4', 'd1', 'ON DAYSHIFT'),
            ('d1', 'd2', ''),
            ('d2', 'd3', '')],
  'notes': ["/today keeps its checklist in today's journal, so an unfinished run can be resumed from any harness; if a real briefing already exists "
            "it only adds updates since. The briefing itself is one '## Daily Briefing' section at the top of the page.",
            '/wrap-up puts its scans in one script: preflight archives the transcript for the overnight learning sweep and kills only orphaned dev '
            "servers and hook workers. Finalize commits and pushes only this session's files, on the default branch, never in a code project; "
            'everything else dirty is reported and left alone.',
            "/handover commits with /wrap-up's rules and stops if dayshift would be missing anything. You copy the transcript, not the agent: 'go' "
            "checks the Mac session has exited, fast-forwards dayshift's repos and resumes the conversation there with the Mac's permissions, never "
            'wider.',
            'Receipts: on your Mac the provenance hook leaves a fingerprint receipt after each agent turn that wrote files, and the commit hook '
            'queues one for repos opted in.',
            "Not verified: which repos are opted in to commit receipts, and whether this morning's briefing ran on your Mac or was written by "
            'Winston first (the command lists both).']},
 {'slug': 'promise-scoreboard',
  'group': 'Checks and assurance',
  'title': 'The promise scoreboard, every night',
  'lede': "Every machine runs the evals behind Datacore's promises for itself, counts only what it could actually run, and speaks only when a "
          'promise moves from green to red or back.',
  'source': 'promise_nightly.py (box-promise-scoreboard 01:40 · hermes-promise-scoreboard 02:10 · nightshift-promise-scoreboard 04:30 · '
            'mac-promise-scoreboard on the first join of the day · nightshift-agent-evals Sun 13:20) · promise_evals.py · needs_gate.py · '
            '.datacore/config/test-needs.yaml · ~/.datacore/state/promise-scoreboard/board-<date>.json, latest.json · fleet_dashboard.py',
  'lanes': ['EACH MACHINE', 'PROMISE_NIGHTLY.PY', 'STATE FILES', 'YOU · THE FIRM GROUP'],
  'steps': [('fire', 0, 0, 'Nightly run, per machine', 'box 01:40 · Miles 04:30', '', '', ''),
            ('evals', 1, 1, 'Run every promise eval', 'promise_evals.py --json', '', '', ''),
            ('gate', 1, 2, 'Could it run here?', 'needs_gate · test-needs.yaml', '', 'focal', ''),
            ('board', 2, 3, "Tonight's board", 'board-<date> · latest.json', '', '', ''),
            ('cmp', 1, 4, 'Compare with history', 'turned red · recovered', '', '', ''),
            ('con', 3, 4, 'Fleet console', 'fleet_dashboard.py', '', '', ''),
            ('chk', 0, 5, 'Contract line checked', 'job_verify · hourly', '', '', 'S'),
            ('msg', 3, 5, 'One message if it moved', 'Telegram · The Firm group', '', '', '')],
  'edges': [('fire', 'evals', ''),
            ('evals', 'gate', 'REDS'),
            ('gate', 'board', 'COUNTED'),
            ('board', 'cmp', ''),
            ('board', 'con', "MILES'S ONLY"),
            ('cmp', 'chk', 'LAST LINE'),
            ('cmp', 'msg', 'MOVED')],
  'notes': ["Each machine keeps its own history: Winston's box at 01:40, Tris's pod at 02:10, Miles at 04:30 and the Mac on its first join of the "
            'day. Miles also runs a weekly agent board on Sundays at 13:20 with real agent sessions, kept separately under '
            'promise-scoreboard/agents/.',
            'A red that needs something this machine lacks (another machine over ssh, a live agent, a module, a file, or since 2026-10-30 a named '
            "machine or a workstation) counts as 'could not run here', never as a regression.",
            'Only movement is announced: a promise that was green on this machine and is red tonight, or the reverse. A red that stays red comes '
            'back only in the Monday summary, and the first night on a machine just starts its history. A message that could not be delivered fails '
            'the job.',
            "The console reads only Miles's board and the night before it. The other machines' boards reach you only through their alerts and their "
            "contract line, which the hourly job check judges (see 'A scheduled job').",
            'Not verified: that the morning briefing shows the scoreboard. No briefing code that reads it was found. The scoreboard writes no ledger '
            "event of its own; the only signed record is its job's hourly verdict."]},
 {'slug': 'audits',
  'group': 'Checks and assurance',
  'title': 'Who audits whom',
  'lede': 'Four agents on four model families audit the code behind the promises. A script with no model judges them the next morning, and a finding '
          'counts only when two different families make it.',
  'source': 'cross_model_audit.py (box-audit-nightly Tue 01:25 · box-audit-calibration Sun 02:25 · box-audit-check 06:40 · box-audit-publish 06:50) '
            '· audit_trio_run.sh (nightshift-drills 02:45 · mac-suite-audit on the first join of a session) · health_audit.py '
            '(nightshift-health-audit 06:30) · reliability_scoreboard.py (box 07:20) · agent_outcomes.py (box Sun 05:45)',
  'lanes': ['AUDITORS · 4 MODEL FAMILIES', 'WINSTON · THE JUDGE', 'SELF-CHECKS · ALERTS', 'YOU'],
  'steps': [('aud', 0, 0, 'Audit on its own night', 'cross_model_audit nightly', '', '', ''),
            ('drill', 2, 0, 'Drills, suites, health', 'audit_trio · health_audit.py', '', '', ''),
            ('find', 0, 1, 'Findings file committed', 'pins validated at write', '', '', ''),
            ('vfy', 2, 1, 'Each machine checks itself', 'job_verify · metric.attest', '', '', 'S'),
            ('chk', 1, 2, '06:40 Check the night', 'audit check · no model', '', '', ''),
            ('rel', 2, 2, '07:20 Reliability score', 'reliability_scoreboard.py', '', '', ''),
            ('pub', 1, 3, '06:50 Two families agree?', 'cluster · publish', '', 'focal', ''),
            ('alert', 2, 3, 'One alert to the group', 'Telegram · The Firm', '', '', ''),
            ('cand', 3, 4, 'You review a candidate', 'audit-candidates.yaml', '', '', '')],
  'edges': [('aud', 'find', ''),
            ('find', 'chk', 'GIT'),
            ('drill', 'vfy', 'ARTIFACT'),
            ('vfy', 'rel', 'VERDICTS'),
            ('chk', 'pub', ''),
            ('chk', 'alert', 'NOT CLEAN'),
            ('rel', 'alert', 'FAILED DAY'),
            ('pub', 'cand', 'CONFIRMED')],
  'notes': ['Each auditor reviews one capability of the promise list once a week, on its own night, by rotation. The model sees the code in its '
            'prompt at fixed commits and has no tools. It writes one findings file and nothing else, under a daily spend cap. A weekly calibration '
            'run on a practice project with planted defects is scored against an answer key.',
            'The judge is never the defendant. The morning check is a script: it reads the findings files, not the code, and never calls a model. A '
            'finding seen by only one family is kept and offered to another family later. Even a confirmed finding is only a candidate eval for you, '
            'never a fix, and it also appears in the briefing.',
            'Drills run in throwaway copies: on Miles at 02:45, the ledger chaos drill (15 injected faults) and the phase-1 flip drill. On the Mac, '
            "after the first join of a session, the laptop-night drill and every module's test suite, because modules have no CI. The check counts "
            "scenarios, not just the exit code. Miles's 06:30 health audit checks disk, git remotes, services, credentials and repository size.",
            "The reliability score is six yes/no conditions computed from files the box already writes, plus each agent's health from its machine's "
            'signed job verdicts; a failed day exits 1, which is itself the alert. On Sundays agent_outcomes scores each agent version and model '
            'from recorded completions.',
            "Not verified: whether the other three agents' audit runs are installed on their machines (the code says they run as cadences; only the "
            "box's jobs are in the inventory), and whether the briefing quotes the agent outcomes table."]},
 {'slug': 'liveness',
  'group': 'Checks and assurance',
  'title': 'Noticing a silent agent',
  'lede': 'A machine that answers is not an agent that works. Hosts are probed from outside; agents prove they are working with a signed check-in, '
          'and silence is named once.',
  'source': 'cos_fleet_probe.sh (box-fleet-probe every 15 min) · box-heartbeat (every 5 min) · hermes-nightshift-deadman (every 15 min) · '
            'venture-heartbeat.sh → venture_heartbeat.py (nightshift, every 30 min) · ledger/checkin.py · ledger/presence.py · '
            'agent_presence_check.py + agent-presence-check.timer (template, every 6 h at :17)',
  'lanes': ['EACH AGENT', 'WINSTON · BOX', 'TRIS · HERMES POD', 'YOU · THE FIRM GROUP'],
  'steps': [('tick', 0, 0, 'Heartbeat tick, 30 min', 'venture_heartbeat.py', '', '', ''),
            ('dead', 2, 0, 'Tris watches Miles', 'dead-man switch, 15 min', '', '', ''),
            ('ci', 0, 1, 'Signed check-in', 'agent.checkin via the hub', '', '', 'S'),
            ('probe', 1, 0, 'Box probes every host', 'TCP :22 · fleetctl status', '', '', ''),
            ('grace', 1, 1, 'Down past the grace?', 'one missed sample: nothing', '', '', ''),
            ('hdown', 3, 2, 'Host down: one alert', 'and one when it is back', '', '', ''),
            ('pres', 1, 3, 'Who stopped checking in?', 'agent_presence_check, 6 h', '', 'focal', ''),
            ('asil', 3, 4, 'Agent silent: one alert', 'then one all-clear', '', '', '')],
  'edges': [('tick', 'ci', 'ONE LINE ADDED'),
            ('probe', 'grace', ''),
            ('grace', 'hdown', 'YES'),
            ('dead', 'hdown', 'MILES QUIET'),
            ('ci', 'pres', 'CHECK-INS'),
            ('pres', 'asil', 'SILENT')],
  'notes': ["On 2026-10-18 the host probe read Data's machine as up all morning while the Data agent itself was stuck. That is why agents now also "
            'append a signed check-in.',
            'Every 15 minutes the box probes each always-on server in the roster (pods through the cluster). A host down past the grace gets one '
            "alert and one on its return, and the log's last line ('fleet presence: ok' or RED) is the job's contract. The box's own 5-minute "
            'heartbeat file is checked only by the box itself; Miles watches the box from outside.',
            'Presence counts a check-in only if its signature verifies for that agent: ok within 1.25 of its declared interval, late within 2, then '
            'silent. The check asks the hub first and falls back to the git copy, naming each silent agent once until it checks in again.',
            "Miles's venture heartbeat contract checks the outcome (0 failed ventures in the last line), not just that a tick happened.",
            'Not verified: the 6-hourly presence check is a template only (nothing in the repository installs it and no scheduled job lists it), and '
            "no heartbeat in the repository calls the check-in, so whether check-ins are sent is unconfirmed. The dead-man script on Tris's pod is "
            'not in the repository, so what it checks and whom it tells is not verified.']},
 {'slug': 'ledger-hygiene',
  'group': 'Ledger',
  'title': 'Ledger housekeeping',
  'lede': 'Hourly jobs keep the ledger fed from what people edit and publish what machines wrote; detectors on Miles check that the whole record is '
          'still sound.',
  'source': 'ledger_ingest_hourly.sh → ledger_ingest_org.py (box-ledger-ingest :00 hourly) · ledger_publish_safe.py (mac-ledger-publish, every join) '
            '· ledger_claim_run.sh (box-ledger-claim :05/:20/:35/:50) · ledger_invariants.py (nightshift-ledger-invariants 03:40) · '
            'detectors/id_churn.py (nightshift-id-churn :40 hourly) · knowledge_lint.py (box-knowledge-lint Sun 06:15)',
  'lanes': ['WINSTON · BOX', 'YOU · MAC', "EVERY SPACE'S LEDGER", 'MILES · OVERNIGHT'],
  'steps': [('pubm', 1, 0, 'Publish ledger files only', 'ledger_publish_safe · join', '', '', ''),
            ('ing', 0, 0, 'Hourly ingest, :00', 'takes the cycle lock', '', '', ''),
            ('skip', 0, 1, 'Last converge failed?', 'then skip that space', '', 'focal', ''),
            ('ev', 0, 2, 'Org edits become events', 'item.create · item.dismiss', '', '', 'S'),
            ('led', 2, 3, 'One log per writer', 'events/*.jsonl', '', '', ''),
            ('churn', 3, 4, 'Duplicate ids, :40 hourly', 'detectors/id_churn.py', '', '', ''),
            ('lint', 0, 6, 'Sun 06:15 Knowledge lint', 'new broken links only', '', '', ''),
            ('inv', 3, 6, '03:40 Whole-ledger truths', 'hashes · chains · forks', '', 'focal', ''),
            ('you', 1, 7, 'One alert when broken', 'Telegram · job contract', '', '', '')],
  'edges': [('pubm', 'led', 'PUSH'),
            ('ing', 'skip', ''),
            ('skip', 'ev', 'NO'),
            ('ev', 'led', ''),
            ('led', 'churn', 'HOURLY'),
            ('led', 'inv', 'NIGHTLY'),
            ('inv', 'you', 'BROKEN'),
            ('lint', 'you', '')],
  'notes': ['The ingest skips any space whose last converge failed and never overlaps an hourly ledger cycle. On 2026-09-26 a stale task list made '
            'it dismiss two new tasks as missing. It writes its log beside the old one and renames it, so a reader never sees a half-written file.',
            'The Mac publishes only machine-written ledger files after each join. Your half-edited files stay exactly as they were, never committed, '
            'stashed or skipped over.',
            "The invariants check every event's hash, every log's chain, that every line parses, that every writer is declared, that no log is "
            'forked and that nothing sits unpublished too long. A space it cannot read reads UNKNOWN, never sound. The id-churn detector watches for '
            'duplicate task ids before the loader regenerates them; on 2026-08-11 that rewrote 1,204 ids.',
            "Winston's claim loop (:05, :20, :35, :50) is the 'Claim and run' step of 'A task's lifecycle': converge, claim, run, converge. Miles's "
            'hourly actor-presence detector checks that every rostered writer still has a log and that it never goes backwards.',
            'Knowledge lint alerts only on links that broke since last week, so the backlog is not repeated. Every detector here, id churn included, '
            "reaches you the same way: its job contract fails and sends one alert (see 'A scheduled job'); the id-churn line is left out of the "
            'drawing only to keep it readable.']},
 {'slug': 'intake-triage',
  'group': 'Intake and knowledge',
  'title': 'Email and GitHub intake, overnight',
  'lede': 'Before the briefing, Miles reads GitHub and Winston reads email. What needs you becomes an inbox entry or a decision board; nothing is '
          'sent and GitHub is never changed.',
  'source': 'nightshift-github-triage 03:15 → modules/github/lib/triage_github.sh, task_creator.py · nightshift-github-triage-report 03:30 → '
            'skills/github-triage/run_nightly.sh (read-only gh shim, triage_board.py validate) · box-email-triage 03:30 → '
            'modules/mail/server/triage-email.sh → email_scanner.py --execute, task_creator.py · github and mail today-hooks (scan caches)',
  'lanes': ['MILES · OVERNIGHT', 'WINSTON · BOX', 'INBOX · REPORTS', 'YOU'],
  'steps': [('cap', 0, 0, '03:15 GitHub capture', 'triage_github.sh', '', '', ''),
            ('rep', 0, 1, '03:30 GitHub report', 'run_nightly.sh · claude -p', '', 'focal', ''),
            ('gin', 2, 1, 'Inbox entries, once', 'inbox.org · :github:', 'TODO', '', ''),
            ('brd', 2, 2, 'Report and board', 'reports/github-triage/', '', '', 'r'),
            ('mail', 1, 2, '03:30 Email triage', 'triage-email.sh · mail', '', '', ''),
            ('you', 3, 3, 'You decide on the board', 'decision board · Mac', '', '', ''),
            ('cls', 1, 3, 'Noise or action?', 'email_scanner.py --execute', '', 'focal', ''),
            ('arc', 1, 4, 'Noise archived', 'Gmail · scan cache', '', '', ''),
            ('ein', 2, 4, 'Inbox entries', 'inbox.org · :email:', 'TODO', '', ''),
            ('bri', 1, 5, 'Briefing: email, GitHub', 'today hooks · scan caches', '', '', '')],
  'edges': [('cap', 'gin', 'MENTIONS'),
            ('rep', 'brd', 'COMMIT'),
            ('brd', 'you', 'BOARD'),
            ('mail', 'cls', ''),
            ('cls', 'arc', 'NOISE'),
            ('cls', 'ein', 'ACTIONABLE'),
            ('arc', 'bri', 'SCAN CACHE')],
  'notes': ['Nothing here sends mail: the nightly email run passes no forwarding address, so invoices are skipped rather than forwarded. Noise is '
            'archived in Gmail; actionable mail stays in Gmail and gets an inbox entry in the space that owns the account.',
            'The GitHub report runs with a read-only gh first on its path: anything that is not a read (issue edit, PR merge, comment, any graphql '
            'call) is refused. It writes one report and one board per scope (public and enterprise), validates each board, commits them to the '
            'personal space and syncs it.',
            "Inbox entries from both jobs then follow 'A task's lifecycle' from the 05:00 inbox run. The GitHub capture also leaves its scan cache, "
            "which the briefing's GitHub section reads.",
            'Both GitHub jobs still run: run_nightly.sh says it replaces triage_github.sh, but the manifest keeps the capture at 03:15 on purpose '
            '(the owner split capture from report on 2026-09-29).',
            "Not verified: that the overnight host's clock is UTC (the manifest's own comment places both GitHub jobs before the briefing, which "
            'assumes it), and whether the briefing ever reads the board files (no reader found besides the decision board and dashboards).']},
 {'slug': 'memory',
  'group': 'Intake and knowledge',
  'title': 'How memory moves',
  'lede': 'Sessions are archived on the Mac, learned from in a batch, and the engrams they produce are merged record by record into one shared store '
          'that every machine pulls.',
  'source': '.claude/settings.json SessionEnd → session_archive.py · /wrap-up preflight · session_learning_sweep.py, session_learning_daily.sh · '
            'sync_traces.sh (mac on arrival; box and Miles 00:10; dayshift 00:20) · extract_recall_events.py (mac-recall-extract, on arrival) · '
            'plur_store_sync.py (mac on join; box-plur-sync :17 and Miles :47 every 2 h) · manifest.yaml (mac-session-learning deleted 2026-10-05)',
  'lanes': ['YOU · MAC', "THE MAC'S MEMORY", 'SHARED ENGRAM STORE', 'WINSTON · MILES'],
  'steps': [('ses', 0, 0, 'A session ends', 'SessionEnd hook · /wrap-up', '', '', ''),
            ('trc', 0, 1, 'Transcripts copied', 'sync_traces.sh · arrival', '', '', ''),
            ('arc', 1, 1, 'Session archived', 'session_archive · pending', '', '', ''),
            ('rec', 0, 2, 'Recalls extracted', 'extract_recall_events.py', '', '', ''),
            ('swp', 1, 2, 'Learning sweep (manual)', 'session_learning_sweep.py', '', 'focal', ''),
            ('eng', 1, 3, 'Engrams written', 'plur_learn · ~/.plur', '', '', ''),
            ('syn', 1, 4, 'Mac store sync, on join', 'plur_store_sync.py', '', '', ''),
            ('rem', 2, 5, 'One shared store', 'merged by engram id', '', 'focal', ''),
            ('srv', 3, 6, 'Box and Miles merge', 'every 2 h · :17 and :47', '', '', ''),
            ('use', 3, 7, 'Recalled next session', 'plur sync reindex', '', '', '')],
  'edges': [('ses', 'trc', 'NEXT ARRIVAL'),
            ('ses', 'arc', 'ARCHIVE'),
            ('trc', 'rec', ''),
            ('arc', 'swp', 'PENDING'),
            ('swp', 'eng', ''),
            ('eng', 'syn', ''),
            ('syn', 'rem', 'PUSH'),
            ('rem', 'srv', 'PULL'),
            ('srv', 'use', '')],
  'notes': ["Each archived session waits as 'pending' in its own meta.json, and the sweep claims each one exactly once, oldest day first. It writes "
            'engrams directly (one claude -p per batch of up to 12 sessions) or writes nothing: there is no candidate queue.',
            'The sweep has no schedule. Its Mac job (io.datacore.session-learning) was retired on 2026-10-05 when the owner cancelled every clocked '
            'job on the Mac, and nothing in the join duties replaced it; wrap-up.yaml still says it runs at 05:20. Archived sessions pile up as '
            "'pending' until someone runs it.",
            "The store sync merges by record, not by line, so two machines that both learned something no longer conflict; engrams scoped 'local' "
            'stay on their machine. It writes counts and commit ids to plur-sync.json, never engram content.',
            "Transcripts are copied on every machine: the Mac into the personal space's traces folder (which the recall extractor reads), the box, "
            'Miles and dayshift beside their own state.',
            'Not verified: whether the sweep has been run by hand since 2026-10-05, and what reads the recall records in ~/.plur/observations (no '
            'reader found in this repository).']},
 {'slug': 'health-into-briefing',
  'group': 'Intake and knowledge',
  'title': 'Health data into the briefing',
  'lede': "The Practice on the Mac imports the night's ring and scale data, and publishes a small fragment to Winston. Only a capacity band, today's "
          "session and a few signals cross; a reading that is not today's is reported as not available.",
  'source': 'mac-health-sync (arrival after 07:30) → health-dashboard/scripts/backfill.py · storage/metrics_store.py · mac-practice-fragment '
            '(launchd one.datacore.practice.fragment, every 900 s) → publish_briefing.py --ingest, lib/briefing.py (schema 1) · box cos_morning.sh → '
            'cos_oura_gate.py · cos_morning_run.py → cos_health_refresh.py',
  'lanes': ['OURA · WITHINGS', 'THE PRACTICE · MAC', 'WINSTON · BOX', 'YOU'],
  'steps': [('ring', 0, 0, 'Ring and scale sync', 'vendor cloud', '', '', ''),
            ('imp', 1, 1, 'Daily health import', 'backfill.py · after 07:30', '', '', ''),
            ('sto', 1, 2, 'Metrics store', 'metrics/<month>.parquet', '', '', ''),
            ('frg', 1, 3, 'Fragment composed', 'publish_briefing · 15 min', '', 'focal', ''),
            ('dlv', 2, 4, 'Fragment delivered', 'fragments/<day>/health.json', '', '', ''),
            ('gate', 2, 5, 'Gate waits for a reading', 'cos_oura_gate.py', '', '', ''),
            ('bri', 2, 6, 'Capacity in the briefing', 'missing or stale: named', '', 'focal', ''),
            ('ref', 2, 7, 'Hourly refresh', 'cos_health_refresh.py', '', '', ''),
            ('you', 3, 7, 'You read the briefing', 'journal · audio', '', '', '')],
  'edges': [('ring', 'imp', 'LAST 3 DAYS'),
            ('imp', 'sto', ''),
            ('sto', 'frg', 'READINESS'),
            ('frg', 'dlv', 'RSYNC'),
            ('dlv', 'gate', ''),
            ('gate', 'bri', 'READING OR 09:00'),
            ('bri', 'ref', 'LATER READING'),
            ('bri', 'you', '')],
  'notes': ["The fragment is deterministic (no model, no network) and a few hundred bytes: a capacity band, today's planned session and up to three "
            'clinical signals. Never a lab value, a document or a diagnosis. The red band, the only thing Winston may cancel a calendar item on, '
            'fires on a critical finding and nothing else.',
            "If the readiness reading is not dated today it is dropped and the fragment says 'stale', so yesterday's sleep never drives today's "
            "plan. A fragment with no reading is still published, so Winston can tell 'the ring has not synced' from 'the Practice is not running'.",
            'Every 15 minutes while the Mac is awake the publisher also tops up from Oura, but only between 05:00 and 12:00 local and only while '
            'today has no reading yet.',
            'The gate holds the briefing until 08:15 Ljubljana and waits for a reading until 09:00, then goes ahead regardless: no health is '
            "reported as absent, never as all-clear. While waiting with no reading it runs the same publisher on the box against the box's own Oura "
            'access, and a later delivery from the Mac overwrites it. The hourly catch-up then replaces the Good Morning section once a newer '
            'reading lands, with one short Telegram note and no second audio.',
            "Not verified: that the box's copy of the publisher and its Oura access work today (this run read code only), and that the daily "
            "import's 07:30 is meant as Mac local time (the manifest gives no zone)."]},
 {'slug': 'lens',
  'group': 'Intake and knowledge',
  'title': 'Lens: behavioural observation',
  'lede': 'A daemon on the Mac records how you work (sessions, tasks, commits, memory events) into a local store. Winston turns a copy of it into a '
          'profile, and the briefing shows one line of it only while it is fresh.',
  'source': 'modules/lens (phase A: capture) · mac-lens-sync: launchd com.datacore.lens, python -m lib.runner --serve · emitters ai_session, task, '
            'workspace, engram · mac-lens-push (on join) → sqlite_push.py · box-lens-analyze 03:55 → lens_analyze.py --profile-out (chief-of-staff '
            'server/lib) · morning_brief.py lens_bullet',
  'lanes': ['YOU · MAC', 'LENS · MAC', 'WINSTON · BOX', 'YOUR BRIEFING'],
  'steps': [('work', 0, 0, 'You work', 'sessions · tasks · commits', '', '', ''),
            ('cap', 1, 1, 'Observed continuously', 'lens runner · 4 emitters', '', '', ''),
            ('db', 1, 2, 'Redacted and stored', 'lens/observations.db', '', 'focal', ''),
            ('aud', 0, 3, 'You can inspect it', 'lens audit · lens redact', '', '', ''),
            ('push', 1, 3, 'Copied to the box', 'sqlite_push.py · on join', '', '', ''),
            ('bdb', 2, 4, "The box's copy", 'lens/observations.db', '', '', ''),
            ('ana', 2, 5, '03:55 Lens analysis', 'lens_analyze.py', '', '', ''),
            ('prof', 2, 6, 'Profile, not events', 'cos/lens/profile.json', '', 'focal', ''),
            ('line', 3, 7, 'One line, if fresh', 'morning_brief lens_bullet', '', '', '')],
  'edges': [('work', 'cap', 'OBSERVED'),
            ('cap', 'db', 'REDACTED'),
            ('db', 'aud', 'AUDIT'),
            ('db', 'push', ''),
            ('push', 'bdb', 'WHOLE FILE'),
            ('bdb', 'ana', ''),
            ('ana', 'prof', '14-DAY WINDOW'),
            ('prof', 'line', 'FRESH?')],
  'notes': ["It reads Claude Code session files, the task lists in every space, git commits under ~/Data and PLUR's engram history. Secrets and "
            'personal data are scrubbed before anything is stored.',
            'The briefing shows the profile only when the last observation is at most 3 days old and the analysis ran in the last 48 hours; '
            'otherwise it says the profile is stale and applies no behavioural signal. No profile at all shows nothing.',
            'Phase A only: nothing turns lens data into engrams or into instructions for agents yet, although the module describes both as its '
            'purpose.',
            "The module's own rule says raw events never leave the device, but the push copies the whole observation store to the box, and the "
            'profile is built there. Only the profile reaches the briefing.',
            "Not verified: that the box runs the newer analyser (the Mac's .datacore/lib copy predates --profile-out; the deploy links the box's "
            "copy to the chief-of-staff version), and that profile.json is fresh today. The Mac's daemon is only checked for the store existing, not "
            'for growing.']}]

# Sidebar and page order: groups by what they do, the ones that break most first.
GROUP_ORDER = ["Work", "Delegation and overnight", "You", "Ledger", "Schedules", "Checks and assurance",
               "Intake and knowledge", "Fleet operations"]
FLOWS.sort(key=lambda f: GROUP_ORDER.index(f["group"]))   # stable: keeps the order within a group


# -- drawing ---------------------------------------------------------------------------

def _elbow(x1, y1, x2, y2, r=8, ym=None) -> str:
    """Down from (x1,y1), across at height ym (default the gap's middle), down into (x2,y2); quarter-arc bends."""
    if x1 == x2:
        return f"M{x1},{y1} V{y2}"
    ym = y1 + (y2 - y1) // 2 if ym is None else ym
    sx = 1 if x2 > x1 else -1
    return (f"M{x1},{y1} V{ym - r} Q{x1},{ym} {x1 + sx * r},{ym} "
            f"H{x2 - sx * r} Q{x2},{ym} {x2},{ym + r} V{y2}")


def svg_flow(f: dict) -> str:
    for st in f["steps"]:
        if len(st[3]) > MAX_NAME or len(st[4]) > MAX_SUB:
            raise ValueError(f"{f['slug']}/{st[0]}: label too long for a box: {st[3]!r} / {st[4]!r}")
        unknown = set(st[7] if len(st) > 7 else "") - set(MARKS)
        if unknown:
            raise ValueError(f"{f['slug']}/{st[0]}: unknown mark(s) {sorted(unknown)}")
    lanes = f["lanes"]
    rows = max(s[2] for s in f["steps"]) + 1
    W = PAD * 2 + LANE_W * len(lanes)
    H = TOP + rows * ROW + 24               # the key is in the sidebar, once for every diagram
    cx = lambda lane: PAD + lane * LANE_W + LANE_W // 2  # noqa: E731
    ty = lambda row: TOP + 32 + row * ROW  # noqa: E731
    pos = {s[0]: (s[1], s[2]) for s in f["steps"]}
    out = [f'<svg viewBox="0 0 {W} {H}" width="{W}" role="img" aria-labelledby="{f["slug"]}-title {f["slug"]}-desc" '
           f'class="flow" xmlns="http://www.w3.org/2000/svg">',
           f'<title id="{f["slug"]}-title">{esc(f["title"])}</title>',
           f'<desc id="{f["slug"]}-desc">{esc(f["lede"])}</desc>',
           '<defs><marker id="ar-' + f["slug"] + '" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto">'
           '<polygon points="0 0, 10 4, 0 8" class="mk"/></marker></defs>']
    # lanes
    for i, name in enumerate(lanes):
        x = PAD + i * LANE_W
        if i:
            out.append(f'<line x1="{x}" y1="{TOP - 40}" x2="{x}" y2="{TOP + rows * ROW}" class="lane-rule"/>')
        out.append(f'<text x="{x + LANE_W // 2}" y="{TOP - 48}" class="lane" text-anchor="middle">{esc(name)}</text>')
    out.append(f'<line x1="{PAD}" y1="{TOP - 32}" x2="{W - PAD}" y2="{TOP - 32}" class="lane-rule"/>')
    # edges first (z-order): fan attach points when a box has two outgoing handoffs
    outs: dict[str, list] = {}
    for e in f["edges"]:
        outs.setdefault(e[0], []).append(e)
    for src, es in outs.items():
        es = sorted(es, key=lambda e: pos[e[1]][0])   # leftmost target takes the leftmost attach point: no crossings
        for k, (a, b, label) in enumerate(es):
            la, ra = pos[a]
            lb, rb = pos[b]
            n = len(es)
            x1 = cx(la) + (0 if n == 1 else int(BOX_W * (k + 1) / (n + 1)) - BOX_W // 2)
            x1 -= x1 % 4
            y1 = ty(ra) + BOX_H
            x2, y2 = cx(lb), ty(rb) - 4
            if n > 1 and la == lb:   # a fanned handoff into the box straight below drops straight, label beside it
                x2 = x1
            # fanned handoffs cross at different heights, so two lines never share a run
            ym = y1 + (y2 - y1) // 2 + ((n - 1 - k) * 24 if n > 1 else 0)
            out.append(f'<path d="{_elbow(x1, y1, x2, y2, ym=ym)}" class="edge" marker-end="url(#ar-{f["slug"]})"/>')
            if label:
                tw = 12 + int(len(label) * 8.4)
                tw += (-tw) % 4
                if x1 == x2:   # vertical: beside the line, 8px gap
                    lx, ly = x1 + 8, y1 + (y2 - y1) // 2 - 12
                    out.append(f'<rect x="{lx}" y="{ly}" width="{tw}" height="24" rx="2" class="mask"/>'
                               f'<text x="{lx + 6}" y="{ly + 16}" class="elabel">{esc(label)}</text>')
                else:          # across: centred above the horizontal run, 8px gap
                    mx = (x1 + x2) // 2 if n == 1 or k == 0 else x2 - 8 - int(len(label) * 4.2) - 12
                    mx -= mx % 4
                    ly = ym - 32 if (n == 1 or k == n - 1) else ym + 8   # a lowered fan line carries its label below it
                    out.append(f'<rect x="{mx - tw // 2}" y="{ly}" width="{tw}" height="24" rx="2" class="mask"/>'
                               f'<text x="{mx}" y="{ly + 16}" class="elabel" text-anchor="middle">{esc(label)}</text>')
    # boxes: a hover title carries the full step; marks sit on the top edge, left of the arrow
    for st in f["steps"]:
        sid, lane, row, name, sub, state, kind = st[:7]
        marks = st[7] if len(st) > 7 else ""
        x, y = cx(lane) - BOX_W // 2, ty(row)
        cls = {"focal": "box focal", "new": "box new"}.get(kind, "box")
        tip = f"{name} — {sub}" + "".join(f"\n{MARKS[c][1]}: {MARKS[c][2]}" for c in marks)
        out.append(f'<g class="step"><title>{esc(tip)}</title>'
                   f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="6" class="paper"/>'
                   f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="6" class="{cls}"/>'
                   f'<text x="{x + 16}" y="{y + 44}" class="name">{esc(name)}</text>'
                   f'<text x="{x + 16}" y="{y + 72}" class="sub">{esc(sub)}</text></g>')
        for k, c in enumerate(marks):
            how, letter, text = MARKS[c]
            mx = x + 24 + k * 32
            out.append(f'<g class="mark {how}" data-k="{letter}"><title>{esc(text)}</title>'
                       f'<circle cx="{mx}" cy="{y}" r="12"/>'
                       f'<text x="{mx}" y="{y + 5}" text-anchor="middle">{letter}</text></g>')
        if state:
            sw = 16 + len(state) * 9
            sw += (-sw) % 4
            out.append(f'<rect x="{x + BOX_W - sw - 8}" y="{y - 12}" width="{sw}" height="24" rx="2" class="state"/>'
                       f'<text x="{x + BOX_W - sw // 2 - 8}" y="{y + 5}" class="statet" text-anchor="middle">{esc(state)}</text>')
    out.append("</svg>")
    return "\n".join(out)


STATES = [
    ("TODO", "item.create → created", "Captured; in an inbox or a list", "You, an agent's capture, a cadence"),
    ("NEXT", "created (approved)", "Committed work; agents may take it", "You, or the morning delegation review"),
    ("WAITING", "created (blocked)", "Waits on someone or a date", "You"),
    ("REVIEW", "item.complete → completed", "Done by an agent; waits for your check", "The agent that ran it"),
    ("DONE", "item.verify → verified", "Finished and checked", "You"),
    ("DEFERRED", "kept, with a wake date", "Not now; comes back on its date", "You"),
    ("CANCELLED", "item.dismiss → dismissed", "Dropped, or superseded", "You, or housekeeping"),
]

KIND_TEXT = {
    "same-output": ("Two jobs write one shared file", "Risk: they overwrite each other, or one fault opens two repairs."),
    "same-process": ("One process scheduled on two machines", "Risk: the work runs twice (the delegation review case)."),
    "no-contract": ("Scheduled with no job contract", "Nothing checks that it ran; a silent failure stays silent."),
    "contract-not-in-shared-manifest": ("Contract only in a host-local manifest", "Checked on that host, invisible here."),
    "cadence-not-in-shared-manifest": ("Venture cadence (declared in venture.yaml)", "Judged by cadence liveness, not a job contract."),
}


def fig(svg: str) -> str:
    """A diagram with its zoom bar: smaller, larger, fit the whole diagram on screen, full width."""
    bar = ('<div class="zoom" role="toolbar" aria-label="Zoom this diagram">'
           '<button type="button" data-zoom="out" title="Smaller" aria-label="Smaller">−</button>'
           '<button type="button" data-zoom="in" title="Larger" aria-label="Larger">+</button>'
           '<button type="button" data-zoom="fit" title="Fit the whole diagram on screen">Fit to screen</button>'
           '<button type="button" data-zoom="full" title="Back to the default size">Reset</button></div>')
    return f'<div class="figwrap">{bar}<div class="fig">{svg}</div></div>'


def upgrade_flow(f: dict) -> dict:
    """The proposed version of a flow, in the same shape svg_flow draws (own slug, so its ids never clash)."""
    up = UPGRADES[f["slug"]]
    return {"slug": f["slug"] + "-up", "title": f["title"] + " (proposed)", "lede": up["summary"],
            "lanes": up["lanes"], "steps": up["steps"], "edges": up["edges"]}


NEED_CLASS = {"your decision": "dec", "DIP amendment": "dip", "code fix": "fix", "install": "ins"}


def upgrade_pane(f: dict) -> str:
    up = UPGRADES[f["slug"]]
    head = '<p class="proposed">Proposed, not running</p>'
    if up.get("keep"):
        return f'{head}<p class="lede"><b>{esc(up["summary"])}</b> {esc(up["keep"])}</p>'
    items = "".join(f'<li><span class="need {NEED_CLASS[n]}">{esc(n)}</span> {esc(w)}<br><span class="why">{esc(y)}</span></li>'
                    for w, y, n in up["changes"])
    return (f'{head}<p class="lede">{esc(up["summary"])}</p>{fig(svg_flow(upgrade_flow(f)))}'
            f'<h3 class="ch">What changes, and what each change needs</h3><ol class="changes">{items}</ol>')


def upgrades_markdown() -> str:
    """The proposals as a report, in page order (process_views.py --report)."""
    out = ["# Process upgrades: proposals for every process (2026-10-31)", "",
           "Owner, 2026-10-31: review the diagrams, suggest improvements in processes, and give each process a "
           "Current / Upgrade toggle. Each proposal is drawn on the processes page "
           "(`1-tracks/dev/architecture/processes.html`, switch to Upgrade). Nothing has been changed in the running fleet.", "",
           "Each change names what it needs: **your decision**, a **DIP amendment** for you to ratify, a **code fix**, "
           "or an **install** (the code exists; nothing schedules or wires it).", ""]
    group = None
    for f in FLOWS:
        if f["group"] != group:
            group = f["group"]
            out += [f"## {group}", ""]
        up = UPGRADES[f["slug"]]
        out += [f"### {f['title']}", "", up["summary"], ""]
        if up.get("keep"):
            out += [up["keep"], ""]
            continue
        for w, y, n in up["changes"]:
            out.append(f"- **{n}**: {w} *Why:* {y}")
        out.append("")
    return "\n".join(out)


def overlap_section(inv: dict) -> str:
    groups: dict[str, list] = {}
    for f in inv.get("overlaps") or []:
        groups.setdefault(f["kind"], []).append(f)
    rows = []
    rows.append('<tr class="fixed"><td>Morning delegation review</td><td>chief-of-staff review</td>'
                '<td>Winston 23:30 and Miles 05:55 (cos-cadence-triage.timer)</td>'
                '<td>Fixed 2026-10-30: Miles\'s timer off, Winston owns it</td></tr>')
    for kind in ("same-process", "same-output"):
        for f in groups.get(kind, []):
            why = KIND_TEXT[kind][1]
            owners = {b.rsplit("(", 1)[-1] for b in f["by"]}
            if kind == "same-output" and any("-pull" in b for b in f["by"]):
                why = "A one-way copy pulled onto another machine; expected, not two writers."
            elif kind == "same-output" and len(owners) == 1:
                why = "One run judged by two job contracts: a single fault opens two repair tasks."
            rows.append(f"<tr><td>{esc(KIND_TEXT[kind][0])}</td><td><code>{esc(f['what'])}</code></td>"
                        f"<td>{esc(', '.join(f['by']))}</td><td>{esc(why)}</td></tr>")
    gaps = []
    for kind in ("no-contract", "contract-not-in-shared-manifest", "cadence-not-in-shared-manifest"):
        items = groups.get(kind, [])
        if not items:
            continue
        where: dict[str, list] = {}
        for f in items:
            where.setdefault(", ".join(f["by"]), []).append(f["what"])
        detail = "; ".join(f"<b>{esc(w)}</b>: " + ", ".join(f"<code>{esc(x)}</code>" for x in sorted(v)) for w, v in where.items())
        gaps.append(f'<details><summary><span class="n">{len(items)}</span> {esc(KIND_TEXT[kind][0])}'
                    f' <span class="why">— {esc(KIND_TEXT[kind][1])}</span></summary><p>{detail}</p></details>')
    return (f'<table class="ov"><thead><tr><th>Overlap</th><th>What</th><th>Who runs or writes it</th><th>Why it matters</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            f'<h3>Gaps: processes nothing checks</h3>{"".join(gaps)}'
            f'<p class="src">Generated {esc(inv.get("generated", ""))} from {esc(inv.get("source", ""))}. '
            f'Every machine also runs its own ledger cycle, job checker, promise scoreboard and syncs: those copies are by design and not listed.</p>')


CSS = """
:root{--paper:#fafaf9;--paper-2:#f5f5f0;--ink:#1a1a1a;--muted:#4b4b4b;--soft:#6e6e6d;--rule:rgba(26,26,26,.10);
--rule-solid:rgba(26,26,26,.22);--accent:#0e7490;--accent-tint:rgba(14,116,144,.08);--link:#6d4bc7;--sig:#6d4bc7;--prov:#b45309}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--paper:#0e0f14;--paper-2:#16181f;--ink:#f0f0f2;--muted:#c5c5c7;
--soft:#8d8d90;--rule:rgba(240,240,242,.10);--rule-solid:rgba(240,240,242,.22);--accent:#22d3ee;--accent-tint:rgba(34,211,238,.12);
--link:#a78bfa;--sig:#a78bfa;--prov:#f0a050}}
:root[data-theme="dark"]{--paper:#0e0f14;--paper-2:#16181f;--ink:#f0f0f2;--muted:#c5c5c7;--soft:#8d8d90;--rule:rgba(240,240,242,.10);
--rule-solid:rgba(240,240,242,.22);--accent:#22d3ee;--accent-tint:rgba(34,211,238,.12);--link:#a78bfa;--sig:#a78bfa;--prov:#f0a050}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:'Outfit',system-ui,sans-serif;font-size:17px;line-height:1.5}
header.top{position:sticky;top:0;z-index:3;height:56px;background:var(--paper);border-bottom:1px solid var(--rule);padding:0 24px;display:flex;
gap:12px;align-items:center}
header.top a{color:var(--ink);text-decoration:none;font-size:15px;padding:5px 12px;border:1px solid var(--rule-solid);border-radius:6px}
header.top a:hover,header.top a.on{border-color:var(--accent);color:var(--accent)}
header.top .eyebrow{font-family:'JetBrains Mono',monospace;font-size:12px;letter-spacing:.14em;color:var(--soft);margin-right:8px}
.wrap{display:grid;grid-template-columns:272px minmax(0,1fr);max-width:1760px;margin:0 auto}
nav.side{position:sticky;top:56px;height:calc(100vh - 56px);overflow-y:auto;border-right:1px solid var(--rule);padding:20px 16px 32px}
nav.side .g{font-family:'JetBrains Mono',monospace;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--soft);margin:20px 10px 6px}
nav.side .g:first-child{margin-top:0}
nav.side a{display:block;padding:6px 10px;margin:1px 0;border-radius:6px;color:var(--muted);font-size:15px;line-height:1.35;text-decoration:none}
nav.side a:hover{color:var(--ink);background:var(--paper-2)}
nav.side a.on{color:var(--accent);background:var(--accent-tint)}
.key{margin:24px 10px 0;padding-top:16px;border-top:1px solid var(--rule);font-size:13px;color:var(--muted)}
.key div{display:flex;gap:10px;align-items:flex-start;margin:8px 0;line-height:1.35}
.key svg{flex:none}
main{padding:8px 40px 64px;min-width:0}
h1{font-family:'Literata',Georgia,serif;font-weight:300;font-size:36px;margin:24px 0 8px}
h2{font-family:'Literata',Georgia,serif;font-weight:400;font-size:28px;margin:56px 0 8px;scroll-margin-top:72px}
h3{font-size:20px;font-weight:600;margin:28px 0 10px}
.lede{font-size:18px;color:var(--muted);max-width:76ch;margin:0 0 20px}
.src{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--soft);max-width:120ch}
.fig{overflow:auto;margin:0 0 12px}
.figwrap{position:relative;margin:8px 0 0;scroll-margin-top:60px}
.zoom{display:flex;gap:4px;justify-content:flex-end;position:sticky;top:64px;z-index:2;margin:0 0 4px}
.zoom button,.sidebtn{font:inherit;font-size:13px;color:var(--muted);background:var(--paper);border:1px solid var(--rule-solid);
border-radius:6px;padding:3px 10px;cursor:pointer;min-width:32px}
.zoom button:hover,.sidebtn:hover{color:var(--accent);border-color:var(--accent)}
.sidebtn{font-size:14px;color:var(--ink)}
body.side-off .wrap{grid-template-columns:minmax(0,1fr);max-width:none}
body.side-off nav.side{display:none}
ul.notes{max-width:96ch;padding-left:20px;font-size:16px}ul.notes li{margin:6px 0}
svg.flow{max-width:100%;height:auto;display:block}
svg.flow.zoomed{max-width:none}
svg .paper{fill:var(--paper)} svg .box{fill:var(--paper);stroke:var(--ink);stroke-width:1.2}
svg .box.focal{fill:var(--accent-tint);stroke:var(--accent);stroke-width:2}
svg .box.new{stroke:var(--accent);stroke-width:2;stroke-dasharray:7 5}
.switch{display:inline-flex;border:1px solid var(--rule-solid);border-radius:8px;overflow:hidden;margin:4px 0 14px}
.switch input{position:absolute;opacity:0;pointer-events:none}
.switch label{padding:6px 16px;font-size:15px;cursor:pointer;color:var(--muted)}
.switch label+input+label{border-left:1px solid var(--rule-solid)}
.switch input:checked+label{background:var(--accent-tint);color:var(--accent);font-weight:600}
.switch input:focus-visible+label{outline:2px solid var(--accent);outline-offset:-2px}
.pane.up{display:none}
section.flow-sec[data-view="up"] .pane.cur{display:none} section.flow-sec[data-view="up"] .pane.up{display:block}
section.flow-sec:has(input[value="up"]:checked) .pane.cur{display:none}
section.flow-sec:has(input[value="up"]:checked) .pane.up{display:block}
.proposed{display:inline-block;font-family:'JetBrains Mono',monospace;font-size:12px;letter-spacing:.14em;text-transform:uppercase;
color:var(--prov);border:1px dashed var(--prov);border-radius:4px;padding:2px 8px;margin:0 0 10px}
h3.ch{font-size:18px;margin:18px 0 6px}
ol.changes{max-width:96ch;padding-left:22px;font-size:16px}ol.changes li{margin:10px 0}
ol.changes .why{color:var(--muted);font-size:15px}
.need{font-family:'JetBrains Mono',monospace;font-size:11px;letter-spacing:.06em;text-transform:uppercase;padding:1px 6px;
border-radius:4px;border:1px solid currentColor;margin-right:6px;white-space:nowrap}
.need.dec{color:var(--sig)} .need.dip{color:var(--prov)} .need.fix{color:var(--accent)} .need.ins{color:var(--soft)}
header.top .all{margin-left:auto;display:flex;gap:6px;align-items:center;font-size:13px;color:var(--soft)}
header.top .all button{font:inherit;font-size:14px;color:var(--ink);background:none;border:1px solid var(--rule-solid);border-radius:6px;
padding:4px 10px;cursor:pointer}
header.top .all button[aria-pressed="true"]{border-color:var(--accent);color:var(--accent)}
svg .step:hover .box{stroke:var(--accent);stroke-width:2}
svg .name{font-family:'Outfit',sans-serif;font-size:16px;font-weight:600;fill:var(--ink)}
svg .sub{font-family:'JetBrains Mono',monospace;font-size:13px;fill:var(--muted)}
svg .lane{font-family:'JetBrains Mono',monospace;font-size:13px;letter-spacing:.1em;fill:var(--soft)}
svg .lane-rule{stroke:var(--rule-solid);stroke-width:1}
svg .edge{fill:none;stroke:var(--muted);stroke-width:1.6} svg .mk{fill:var(--muted)}
svg .mask{fill:var(--paper)} svg .elabel{font-family:'JetBrains Mono',monospace;font-size:13px;letter-spacing:.04em;fill:var(--soft)}
svg .state{fill:var(--paper);stroke:var(--accent);stroke-width:1.2} svg .statet{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:400;fill:var(--accent)}
.mark circle{fill:var(--paper);stroke:var(--sig);stroke-width:1.8}
.mark text{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;fill:var(--sig)}
.mark.optional circle{stroke-dasharray:3 3}
.mark[data-k="R"] circle{stroke:var(--prov)} .mark[data-k="R"] text{fill:var(--prov)}
.mark{cursor:help}
table{border-collapse:collapse;width:100%;font-size:16px;margin:8px 0}
th{text-align:left;font-family:'JetBrains Mono',monospace;font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--soft);
font-weight:400;border-bottom:1px solid var(--rule-solid);padding:8px 12px}
td{border-bottom:1px solid var(--rule);padding:9px 12px;vertical-align:top}
tr.fixed td{color:var(--soft)} tr.fixed td:last-child{color:var(--accent)}
code{font-family:'JetBrains Mono',monospace;font-size:14px}
details{border-bottom:1px solid var(--rule);padding:10px 0;max-width:120ch}summary{cursor:pointer;font-size:17px}
details .n{display:inline-block;min-width:40px;font-family:'JetBrains Mono',monospace;color:var(--accent)}
details .why{color:var(--soft)} details p{font-size:15px;line-height:1.7}
@media (max-width:900px){.wrap{display:block}nav.side{position:static;height:auto;border-right:0;border-bottom:1px solid var(--rule)}
main{padding:8px 16px 48px}h1{font-size:30px}h2{font-size:24px}header.top{padding:0 16px}}
"""

JS = """
(function(){var links={};document.querySelectorAll('nav.side a').forEach(function(a){links[a.getAttribute('href').slice(1)]=a});
if(!('IntersectionObserver' in window))return;var cur=null;
var io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){if(cur)cur.classList.remove('on');
cur=links[e.target.id];if(cur)cur.classList.add('on')}})},{rootMargin:'-64px 0px -70% 0px'});
document.querySelectorAll('main h2[id]').forEach(function(h){io.observe(h)})})();
(function(){var secs=document.querySelectorAll('section.flow-sec'),btns=document.querySelectorAll('[data-all]');
function show(sec,v){sec.dataset.view=v;var r=sec.querySelector('input[value="'+v+'"]');if(r)r.checked=true}
function all(v){secs.forEach(function(s){show(s,v)});btns.forEach(function(b){b.setAttribute('aria-pressed',b.dataset.all===v)});
try{localStorage.setItem('processes-view',v)}catch(e){}}
secs.forEach(function(s){s.querySelectorAll('.switch input').forEach(function(r){r.addEventListener('change',function(){show(s,r.value)})})});
btns.forEach(function(b){b.addEventListener('click',function(){all(b.dataset.all)})});
var saved=null;try{saved=localStorage.getItem('processes-view')}catch(e){}if(saved==='up')all('up')})();
(function(){var b=document.getElementById('side-toggle');if(!b)return;
function set(off){document.body.classList.toggle('side-off',off);b.setAttribute('aria-expanded',String(!off));
try{localStorage.setItem('processes-side',off?'off':'on')}catch(e){}}
b.addEventListener('click',function(){set(!document.body.classList.contains('side-off'))});
var s=null;try{s=localStorage.getItem('processes-side')}catch(e){}if(s==='off')set(true)})();
document.addEventListener('click',function(e){var t=e.target.closest('[data-zoom]');if(!t)return;
var svg=t.closest('.figwrap').querySelector('svg.flow'),vb=svg.viewBox.baseVal,box=svg.parentNode.clientWidth,a=t.dataset.zoom;
var w=svg.getBoundingClientRect().width;
if(a==='full'){svg.classList.remove('zoomed');svg.style.width='';return}
if(a==='fit'){w=Math.min(box,vb.width*(window.innerHeight-150)/vb.height)}
else{w=w*(a==='in'?1.25:0.8)}
w=Math.max(240,Math.min(w,vb.width*3));svg.classList.add('zoomed');svg.style.width=Math.round(w)+'px';
if(a==='fit')svg.closest('.figwrap').scrollIntoView({block:'start'})});
"""


def _key_badge(letter: str, how: str) -> str:
    dash = ' stroke-dasharray="3 3"' if how == "optional" else ""
    return (f'<svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true"><g class="mark {how}" data-k="{letter}">'
            f'<circle cx="14" cy="14" r="12"{dash}/><text x="14" y="19" text-anchor="middle">{letter}</text></g></svg>')


def sidebar() -> str:
    out, group = [], None
    for f in FLOWS:
        if f["group"] != group:
            group = f["group"]
            out.append(f'<div class="g">{esc(group)}</div>')
        out.append(f'<a href="#{f["slug"]}">{esc(f["title"])}</a>')
        if f["slug"] == "task-lifecycle":
            out.append('<a href="#task-states" style="padding-left:24px;font-size:14px">The seven task states</a>')
    out.append('<div class="g">Fleet</div><a href="#overlaps">Duplicates and overlaps</a>')
    key = ['<div class="key"><div class="g" style="margin:0 0 4px">Key · hover any mark</div>',
           '<div><svg width="28" height="20" aria-hidden="true"><rect x="1" y="1" width="26" height="18" rx="4" '
           'style="fill:var(--accent-tint);stroke:var(--accent);stroke-width:2"/></svg>where it broke or matters most</div>']
    for c, (how, letter, text) in MARKS.items():
        short = {"S": "signed by the writer", "O": "signed by the owner",
                 "C": "your co-sign, only for side effects", "R": "provenance receipt, where the hook is on"}[letter]
        key.append(f'<div title="{esc(text)}">{_key_badge(letter, how)}<span>{esc(short)}'
                   f'{" <i>(optional)</i>" if how == "optional" else ""}</span></div>')
    key.append('<div>Dashed ring: happens only in some cases.</div>')
    key.append('<div><svg width="28" height="20" aria-hidden="true"><rect x="1" y="1" width="26" height="18" rx="4" '
               'style="fill:none;stroke:var(--accent);stroke-width:2;stroke-dasharray:5 3"/></svg>Upgrade view: new or changed step</div></div>')
    return f'<nav class="side" id="side" aria-label="Processes">{"".join(out)}{"".join(key)}</nav>'


def page(inv: dict) -> str:
    body = []
    for f in FLOWS:
        notes = "".join(f"<li>{esc(n)}</li>" for n in f["notes"])
        s = f["slug"]
        switch = (f'<div class="switch" role="radiogroup" aria-label="Current or proposed upgrade">'
                  f'<input type="radio" name="view-{s}" id="{s}-cur" value="cur" checked><label for="{s}-cur">Current</label>'
                  f'<input type="radio" name="view-{s}" id="{s}-up" value="up"><label for="{s}-up">Upgrade</label></div>')
        body.append(f'<section class="flow-sec" data-view="cur"><h2 id="{s}">{esc(f["title"])}</h2>{switch}'
                    f'<div class="pane cur" id="{s}-cur-pane"><p class="lede">{esc(f["lede"])}</p>'
                    f'{fig(svg_flow(f))}<ul class="notes">{notes}</ul>'
                    f'<p class="src">From the code: {esc(f["source"])}</p></div>'
                    f'<div class="pane up" id="{s}-up-pane">{upgrade_pane(f)}</div></section>')
        if f["slug"] == "task-lifecycle":
            rows = "".join(f"<tr><td><b>{a}</b></td><td><code>{esc(b)}</code></td><td>{esc(c)}</td><td>{esc(d)}</td></tr>"
                           for a, b, c, d in STATES)
            body.append('<h3 id="task-states" style="scroll-margin-top:72px">The seven task states (DIP-0009 v2.0) and their ledger events</h3>'
                        '<table><thead><tr><th>State</th><th>Ledger</th><th>Means</th><th>Who moves it here</th></tr></thead>'
                        f'<tbody>{rows}</tbody></table>')
    body.append('<h2 id="overlaps">Duplicates and overlaps</h2><p class="lede">Generated from the job contracts and the live '
                'schedules on every machine. An overlap is the same work, or the same shared file, owned twice.</p>'
                + overlap_section(inv))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Datacore processes</title>
<meta name="description" content="How work moves through Datacore: tasks, the morning, the ledger (admit, fold, seal, corrections, daily checks), sync, scheduled jobs, cadences, repairs, and where processes overlap.">
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=Literata:ital,wght@0,300;0,400;1,300;1,400&family=JetBrains+Mono:wght@300;400;700&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>
<header class="top"><button type="button" id="side-toggle" class="sidebtn" aria-controls="side" aria-expanded="true" title="Hide or show the list of processes">☰ List</button><span class="eyebrow">DATACORE</span><a href="datacore.html">Architecture map</a><a class="on" href="processes.html">Processes</a><span class="all">Show every process as <button type="button" data-all="cur" aria-pressed="true">Current</button><button type="button" data-all="up" aria-pressed="false">Upgrade</button></span></header>
<div class="wrap">{sidebar()}
<main><h1>Processes and workflows</h1>
<p class="lede">The architecture map shows what exists. This page shows how work moves through it: who runs each step, on which machine, what it writes, and where a step is signed or leaves a provenance receipt. Drawn from the code and the schedules; the overlap list is generated. Hover a step or a mark for its full text. Each process has a Current / Upgrade switch: Upgrade shows a proposal, with what each change needs, and nothing in it is running.</p>
{"".join(body)}
</main></div><script>{JS}</script></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inventory", default=str(ARCH / "processes.yaml"))
    ap.add_argument("--out", default=str(ARCH / "processes.html"))
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--report", help="also write the upgrade proposals as a markdown report to this path")
    a = ap.parse_args(argv)
    inv = yaml.safe_load(Path(a.inventory).read_text()) if Path(a.inventory).exists() else {}
    Path(a.out).write_text(page(inv))
    print(a.out)
    if a.report:
        Path(a.report).write_text(upgrades_markdown() + "\n")
        print(a.report)
    if a.open:
        subprocess.run(["open", a.out], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
