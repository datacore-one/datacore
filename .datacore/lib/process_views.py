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

ROOT = Path(__file__).resolve().parents[2]
ARCH = ROOT / "2-datacore" / "1-tracks" / "dev" / "architecture"

# -- geometry (4px grid) ------------------------------------------------------------
LANE_W = 336          # one actor column; 4 lanes + padding = 1392px, the page width
BOX_W, BOX_H = 272, 96
MAX_NAME, MAX_SUB = 26, 28   # characters that fit a box at 18px / 14px mono
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
        "lede": ("From capture to done. Every step is a ledger event; the lists you read "
                 "(inbox.org, next_actions.org) are written from the ledger. The delegation "
                 "review is the step that ran twice until 2026-10-30."),
        "source": "org_workspace_adapter.py add/move · cos_inbox.sh (box-inbox 05:00) · ledger_phase1_cycle.sh (:25 hourly) · "
                  "chief-of-staff lib.cli review (box-delegation-review 23:30) · nightshift run (hourly) · ledger/fold.py · ledger/policy.py",
        "lanes": ["YOU · MAC", "LEDGER · SHARED", "WINSTON · BOX", "MILES · OVERNIGHT"],
        "steps": [
            ("cap", 0, 0, "Capture", "inbox.org · item.create", "TODO", "", "Sc"),
            ("inb", 2, 1, "Inbox run, 05:00", "cos_inbox.sh", "", "", ""),
            ("adm", 1, 2, "Admitted to the ledger", "events/<actor>.jsonl", "", "", "S"),
            ("nxt", 0, 3, "Next actions list", "next_actions.org", "NEXT", "", ""),
            ("rev", 2, 4, "Delegation review, 23:30", "delegation_gate.py", "", "focal", ""),
            ("run", 3, 5, "Claim and run", "item.claim · grant", "", "", "Sr"),
            ("res", 1, 6, "Result recorded", "item.complete", "REVIEW", "", "S"),
            ("ok", 0, 7, "You review it", "item.verify", "DONE", "", "S"),
        ],
        "edges": [("cap", "inb", "NEXT 05:00"), ("inb", "adm", "FILE ACTION"), ("adm", "nxt", "PROJECT"),
                  ("nxt", "rev", "AGENT TASKS"), ("rev", "run", "APPROVED"), ("run", "res", "OUTPUT"),
                  ("res", "ok", "TO REVIEW")],
        "notes": [
            "Approval writes one receipt, 2-datacore/.datacore/reviews/cos-lastrun.json; delegation holds when it is missing or stale.",
            "Until 2026-10-30 Miles ran the same review at 05:55 (cos-cadence-triage.timer on dayshift); both wrote the receipt and the merge read as 'hold'. Miles's copy is off.",
            "A task with a side effect (email, payment, deploy) cannot even be created without your recorded grant: the co-sign mark on Capture. Everything else needs no co-sign.",
            "WAITING, DEFERRED and CANCELLED leave this path: CANCELLED is item.dismiss; DEFERRED keeps the task with a wake date.",
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
        "source": "ledger/log.py EventLog.append · ledger/hub.py HubWriter, Hub.append, Hub.seal · ledger/hub_admission.py check_event · "
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
            "Not verified: how often the hub seals. Nothing in this repository schedules it and the hub process has no job contract, so no checker watches that it runs (a gap).",
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


# -- drawing ---------------------------------------------------------------------------

def _elbow(x1, y1, x2, y2, r=8) -> str:
    """Down from (x1,y1), across at the gap's middle, down into (x2,y2); quarter-arc bends."""
    if x1 == x2:
        return f"M{x1},{y1} V{y2}"
    ym = y1 + (y2 - y1) // 2
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
            out.append(f'<path d="{_elbow(x1, y1, x2, y2)}" class="edge" marker-end="url(#ar-{f["slug"]})"/>')
            if label:
                tw = 12 + int(len(label) * 8.4)
                tw += (-tw) % 4
                if x1 == x2:   # vertical: beside the line, 8px gap
                    lx, ly = x1 + 8, y1 + (y2 - y1) // 2 - 12
                    out.append(f'<rect x="{lx}" y="{ly}" width="{tw}" height="24" rx="2" class="mask"/>'
                               f'<text x="{lx + 6}" y="{ly + 16}" class="elabel">{esc(label)}</text>')
                else:          # across: centred above the horizontal run, 8px gap
                    ym = y1 + (y2 - y1) // 2
                    mx = (x1 + x2) // 2
                    mx -= mx % 4
                    out.append(f'<rect x="{mx - tw // 2}" y="{ym - 32}" width="{tw}" height="24" rx="2" class="mask"/>'
                               f'<text x="{mx}" y="{ym - 16}" class="elabel" text-anchor="middle">{esc(label)}</text>')
    # boxes: a hover title carries the full step; marks sit on the top edge, left of the arrow
    for st in f["steps"]:
        sid, lane, row, name, sub, state, kind = st[:7]
        marks = st[7] if len(st) > 7 else ""
        x, y = cx(lane) - BOX_W // 2, ty(row)
        cls = "box focal" if kind == "focal" else "box"
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
.fig{overflow-x:auto;margin:8px 0 12px}
ul.notes{max-width:96ch;padding-left:20px;font-size:16px}ul.notes li{margin:6px 0}
svg.flow{max-width:100%;height:auto;display:block}
svg .paper{fill:var(--paper)} svg .box{fill:var(--paper);stroke:var(--ink);stroke-width:1.2}
svg .box.focal{fill:var(--accent-tint);stroke:var(--accent);stroke-width:2}
svg .step:hover .box{stroke:var(--accent);stroke-width:2}
svg .name{font-family:'Outfit',sans-serif;font-size:18px;font-weight:600;fill:var(--ink)}
svg .sub{font-family:'JetBrains Mono',monospace;font-size:14px;fill:var(--muted)}
svg .lane{font-family:'JetBrains Mono',monospace;font-size:14px;letter-spacing:.1em;fill:var(--soft)}
svg .lane-rule{stroke:var(--rule-solid);stroke-width:1}
svg .edge{fill:none;stroke:var(--muted);stroke-width:1.6} svg .mk{fill:var(--muted)}
svg .mask{fill:var(--paper)} svg .elabel{font-family:'JetBrains Mono',monospace;font-size:14px;letter-spacing:.04em;fill:var(--soft)}
svg .state{fill:var(--paper);stroke:var(--accent);stroke-width:1.2} svg .statet{font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:400;fill:var(--accent)}
.mark circle{fill:var(--paper);stroke:var(--sig);stroke-width:1.8}
.mark text{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;fill:var(--sig)}
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
    key.append('<div>Dashed ring: happens only in some cases.</div></div>')
    return f'<nav class="side" aria-label="Processes">{"".join(out)}{"".join(key)}</nav>'


def page(inv: dict) -> str:
    body = []
    for f in FLOWS:
        notes = "".join(f"<li>{esc(n)}</li>" for n in f["notes"])
        body.append(f'<h2 id="{f["slug"]}">{esc(f["title"])}</h2><p class="lede">{esc(f["lede"])}</p>'
                    f'<div class="fig">{svg_flow(f)}</div><ul class="notes">{notes}</ul>'
                    f'<p class="src">From the code: {esc(f["source"])}</p>')
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
<header class="top"><span class="eyebrow">DATACORE</span><a href="datacore.html">Architecture map</a><a class="on" href="processes.html">Processes</a></header>
<div class="wrap">{sidebar()}
<main><h1>Processes and workflows</h1>
<p class="lede">The architecture map shows what exists. This page shows how work moves through it: who runs each step, on which machine, what it writes, and where a step is signed or leaves a provenance receipt. Drawn from the code and the schedules; the overlap list is generated. Hover a step or a mark for its full text.</p>
{"".join(body)}
</main></div><script>{JS}</script></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inventory", default=str(ARCH / "processes.yaml"))
    ap.add_argument("--out", default=str(ARCH / "processes.html"))
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args(argv)
    inv = yaml.safe_load(Path(a.inventory).read_text()) if Path(a.inventory).exists() else {}
    Path(a.out).write_text(page(inv))
    print(a.out)
    if a.open:
        subprocess.run(["open", a.out], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
