#!/usr/bin/env python3
"""Winston takes the failure, Miles takes the repair, and you hear about it only
when that does not work.

WHAT CHANGED AND WHY. A recurring job failure used to do two things: file an
unassigned org TODO, and send the operator a Telegram message once a day for as
long as it kept failing. The TODO had no owner, so it aged; the message was
addressed to the one person on the fleet who is not always awake. Nothing in
that loop repaired anything, and a daily message nobody can act on at the moment
they receive it trains the reader to stop reading.

Now the third consecutive failure is DELEGATED: a ledger item addressed to
miles, carrying the failure text, the producer, and a check that decides for
itself whether the repair worked. The operator is told when -- and only when --
that path is exhausted:

    the gate refuses the delegation      → nobody can take it, so say so now
    miles dead-letters it                → three failed attempts, genuinely stuck
    it is still open after ESCALATE_H    → not progressing; a person should look

THE THRESHOLD IS NOT A NEW NUMBER. Three consecutive runs is DIP-0031's
definition of a recurring failure, which job_verify already uses to decide the
failure is real rather than a blip. A second threshold invented here would be a
second definition of the same thing.

WHAT MILES MAY NOT DO is the part that matters. See jobs/fix_check.py: the
repair is judged against the contract as it stood when the work was handed over,
so making the check pass by weakening the check does not count as passing.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

# `jobs.fix_check`, never bare `fix_check`. The bare form resolved only when
# lib/jobs itself was on sys.path -- which the test file arranged and nothing in
# production does. job_verify imports `jobs.autofix` with lib on the path, so
# on every host the first real delegation died with ModuleNotFoundError and
# fell through to "could NOT delegate ... escalating to the operator": the
# first-failure alerts the owner kept receiving on 2026-09-20/21 WERE the
# delegation path, failing at import. Found 2026-09-22; not one delegation
# from job_verify had ever succeeded anywhere.
from jobs.fix_check import contract_sha  # noqa: E402

#: Hours an open repair may sit before the operator is told it is not moving.
ESCALATE_H = 24

MARK = "autofix"


def _space(root: Path) -> Path:
    """Where repair items live: the system space, which is where the jobs are
    (install.yaml roles.system); with no role declared, the first space under
    the root that keeps a ledger; else the personal space."""
    from spaces import space_for
    declared = space_for("system", root)
    if declared:
        return root / declared
    for cand in sorted(root.glob("[0-9]*-*")):
        if (cand / ".datacore" / "events").is_dir():
            return cand
    return root / "0-personal"


ROSTER = LIB.parent / "registry" / "infrastructure.yaml"

#: Where an agent may open a repair PULL REQUEST. The owner's boundary, stated
#: 2026-09-22 ("this is only for datacore"): the core repository, nothing else
#: -- not a module's repository, not anything under another organisation --
#: whatever push rights the account happens to hold. A repair whose producer
#: lives elsewhere is refused up front and a person owns it. Widening this is
#: an owner's decision, made here.
#:
#: It was MERGE_REPOS, "where an agent may merge", until the owner decided no
#: agent merges (2026-09-25/26, AGT-5): the repair stops at an open PR and the
#: repository's merge gate (the owner by default) merges it. No repository is
#: one an agent may merge into; tool_effects.yaml code.merge refuses the call.
PR_REPOS = frozenset({"datacore-one/datacore"})

#: How a repair item's check names its judge. ledger_claim runs the check in a
#: worktree of the SPACE the item lives in (2-datacore on the box), a separate
#: repository with no `.datacore/lib/`; the relative path never existed there,
#: and every box repair dead-lettered on "can't open file" (winston, 2026-10-02).
#: The checker is the installation's code, so it is named through the root the
#: claim loop exports, resolved on whichever host runs the check.
FIX_CHECK = '"${DATACORE_ROOT:-$HOME/Data}/.datacore/lib/jobs/fix_check.py"'


# ── why a job failed, and whether to try again (W1, 2026-10-23) ──────────────
#
# 676 failed repair attempts in two weeks, against about 43 successes: each day
# filed a FRESH `autofix-<job>-<date>` item, so the three-attempt dead-letter
# bounded one item and never the job, and the same 15 jobs were retried three
# times a day for up to 30 days. About 60 attempts on 2026-10-21 failed on the
# Claude weekly limit and were counted as failed repairs; host-state faults (a
# store over its ceiling, a checkout behind upstream) went to an agent that
# cannot fix them from a fresh copy. So a failure is classified first, and the
# breaker is per JOB and per FINGERPRINT of what the job is, read from the
# ledger every host shares -- not per item, and not a side file.

USAGE_LIMIT, HOST_STATE, CODE_FAULT = "usage_limit", "host_state", "code_fault"

#: Failure text that names the HOST, not the producer's code: a person (or a
#: deploy) has to act on the machine, and no commit an agent makes in a fresh
#: checkout changes it. Narrow and literal on purpose: every miss here is still
#: caught by the breaker after three attempts, while a false hit sends a code
#: fault to the owner instead of the repairer.
HOST_STATE_MARKERS = (
    "behind upstream",            # checks.check_repo_sync: the host has not pulled
    "repo sync check failed",
    "no space left on device",
    "disk is full", "disk full",
    "size ceiling", "over its ceiling",
    "permission denied",
    "stalelogerror", "already wrote seq",   # a rewound ledger: owner-only recovery
    "differs from the deployed", "deploy drift",
)

#: Failed repair attempts on one job and fingerprint, summed across every item
#: and day, after which no new repair is filed. DIP-0031's recurring threshold
#: (and ledger_claim's MAX_ATTEMPTS): one number for one idea.
from jobs.recurrence import RECURRING_AFTER as BREAK_AFTER  # noqa: E402

#: Release kinds that are not an attempt at the repair (ledger_claim's
#: NOT_AN_ATTEMPT plus the usage limit, which is the budget, not the code).
NOT_AN_ATTEMPT = ("infrastructure", "waiting_on_owner", USAGE_LIMIT)

#: How long a usage-limit failure pauses new repairs: the dispatcher's probe
#: interval (claim_backoff.PROBE_AFTER), so the two pauses end together.
USAGE_PAUSE_H = 6


def classify_failure(text: str, job=None) -> str:
    """usage_limit | host_state | code_fault for one failure's text.

    A job may declare `repair: host` in the manifest: its failures are always
    the host's (a detector whose finding is the machine's state)."""
    if getattr(job, "repair", None) == "host":
        return HOST_STATE
    import claim_backoff
    if claim_backoff.is_usage_limit(text):
        return USAGE_LIMIT
    low = (text or "").lower()
    if any(m in low for m in HOST_STATE_MARKERS):
        return HOST_STATE
    return CODE_FAULT


def _producer_files(job, root: Path) -> list[Path]:
    """Files under the installation that the job's cmd names (its code)."""
    import shlex
    try:
        tokens = shlex.split(str(getattr(job, "cmd", "") or ""))
    except ValueError:
        tokens = str(getattr(job, "cmd", "") or "").split()
    out = []
    for t in tokens:
        if ".datacore/" not in t:
            continue
        p = root / t[t.index(".datacore/"):]
        if p.is_file() and p not in out:
            out.append(p)
    return out


def fingerprint(job, root: Path, manifest: Path | None = None) -> str:
    """What the job IS: its contract plus the bytes of the code its cmd names.

    The breaker is keyed by this, so changing the contract or the producer
    reopens it, and nothing else does."""
    import hashlib
    h = hashlib.sha256((contract_sha(job.name, manifest or LIB / "jobs" / "manifest.yaml") or "").encode())
    for p in _producer_files(job, root):
        try:
            h.update(str(p.relative_to(root)).encode() + b"\0" + p.read_bytes())
        except OSError:
            continue
    return h.hexdigest()[:16]


def _hlc_ms(hlc) -> float:
    try:
        return float(str(hlc).split(".")[0])
    except (ValueError, TypeError):
        return 0.0


def breaker(root: Path, job: str, fp: str, *, now_ms: float | None = None) -> dict:
    """{'open', 'attempts', 'usage_pause', 'since'} for one job and fingerprint.

    open         BREAK_AFTER or more failed repair attempts on items carrying
                 this fingerprint since the job last passed verification;
    usage_pause  the latest repair outcome anywhere was a usage limit, less than
                 USAGE_PAUSE_H ago (nothing more runs until it resets).

    Items from before fingerprints existed are not counted: what they repaired
    cannot be told, so they neither open nor hold the breaker."""
    from ledger.fold import fold
    from ledger.log import read_events

    now = now_ms if now_ms is not None else time.time() * 1000.0
    try:
        events = read_events(_space(root))
        state = fold(events)
    except Exception:  # noqa: BLE001 -- an unreadable ledger keeps today's behaviour
        return {"open": False, "attempts": 0, "usage_pause": False, "since": None}
    applied = {line.rsplit(" item.release: ", 1)[0]
               for i in state.items.values() for line in i.history
               if line.endswith(" item.release: applied")}
    autofix_ids = {iid for iid, it in state.items.items()
                   if (it.payload or {}).get("autofix") or iid.startswith(MARK)}
    mine = {iid for iid in autofix_ids
            if (state.items[iid].payload or {}).get("job") == job
            and (state.items[iid].payload or {}).get("fingerprint") == fp}
    # A pass, or the owner's reset (reset_breaker), starts the count again.
    last_pass = max((_hlc_ms(e.hlc) for e in events
                     if (e.payload or {}).get("job") == job
                     and (((e.payload or {}).get("metric") == "job.verify" and (e.payload or {}).get("ok"))
                          or (e.payload or {}).get("metric") == "job.breaker_reset")),
                    default=0.0)
    attempts, last_outcome = 0, None
    for e in events:
        p = e.payload or {}
        iid = p.get("id")
        if iid not in autofix_ids:
            continue
        if e.type == "item.release" and f"{e.hlc} {e.actor}" in applied:
            last_outcome = (p.get("kind"), _hlc_ms(e.hlc))
            if iid in mine and p.get("kind") not in NOT_AN_ATTEMPT and _hlc_ms(e.hlc) > last_pass:
                attempts += 1
        elif e.type == "item.complete":
            last_outcome = ("complete", _hlc_ms(e.hlc))
    pause = bool(last_outcome and last_outcome[0] == USAGE_LIMIT
                 and now - last_outcome[1] < USAGE_PAUSE_H * 3600_000)
    return {"open": attempts >= BREAK_AFTER, "attempts": attempts, "usage_pause": pause,
            "since": last_pass or None}


def reset_breaker(root: Path, job: str, *, reason: str, actor: str | None = None) -> None:
    """The owner's reset of a tripped breaker: a ledger record, so every host
    sees it. The job gets a fresh BREAK_AFTER attempts, as after a pass."""
    from ledger.log import EventLog
    if not reason.strip():
        raise ValueError("a breaker reset needs a reason")
    if actor is None:
        import actor_identity
        actor = actor_identity.this_actor()
    EventLog(_space(root), actor).append(
        "metric.attest", {"metric": "job.breaker_reset", "job": job, "ok": True, "reason": reason})


def _state_dir() -> Path:
    import os
    return Path(os.environ.get("DATACORE_STATE") or Path.home() / ".datacore" / "state")


def escalate_once(job: str, key: str) -> bool:
    """True the first time `key` is escalated for `job` on this host, else False.

    The job's own host is the one that verifies it and so the one that tells
    the owner; a pass clears the job (clear_escalation), so a later, different
    failure is news again."""
    path = _state_dir() / "autofix-escalated.json"
    try:
        seen = json.loads(path.read_text())
    except Exception:  # noqa: BLE001 -- a missing or broken file has seen nothing
        seen = {}
    if seen.get(job) == key:
        return False
    seen[job] = key
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(json.dumps(seen, indent=1, sort_keys=True))
    except OSError:
        pass
    return True


def clear_escalation(job: str) -> None:
    path = _state_dir() / "autofix-escalated.json"
    try:
        seen = json.loads(path.read_text())
        if seen.pop(job, None) is not None:
            path.write_text(json.dumps(seen, indent=1, sort_keys=True))
    except Exception:  # noqa: BLE001
        pass


def record_outcome(payload: dict, outcome: str, detail: str = "", *, actor: str = "",
                   root: Path | None = None) -> None:
    """One repair's outcome as a learning candidate. Never raises.

    Into the store hooks.py already feeds (.datacore/state/learning_candidates.yaml,
    status pending), so the learning pipeline sees "this job is not
    agent-repairable" or "this fix worked" without a second queue.

    outcome: fixed | failed | paused | waiting | gave_up"""
    import os
    from datetime import datetime, timezone
    try:
        from state_store import YamlStateStore
        root = root or Path(os.environ.get("DATACORE_ROOT") or Path.home() / "Data")
        job = payload.get("job") or ""
        klass = "" if outcome in ("fixed", "waiting") else classify_failure(detail)
        text = (f"Repair of job {job} on {payload.get('machine') or '?'}: {outcome}"
                + (f" ({klass})" if klass else "")
                + (f" -- {detail.strip()[:300]}" if detail.strip() else ""))
        YamlStateStore("learning_candidates.yaml", default=[],
                       data_root=root / ".datacore" / "state").append_to_list([{
                           "agent": actor or payload.get("assignee") or "",
                           "category": "repair_outcome", "source": "autofix",
                           "item": payload.get("id", ""), "job": job,
                           "fingerprint": payload.get("fingerprint") or payload.get("contract_sha") or "",
                           "outcome": outcome, "failure_class": klass, "text": text,
                           "timestamp": datetime.now(timezone.utc).isoformat(),
                           "status": "pending"}])
    except Exception:  # noqa: BLE001 -- learning must not break the dispatcher
        pass


def _artifact_tail(job, n: int = 12) -> str:
    """The job's own last output lines -- the real error -- for the repairer."""
    import datetime as _dt
    import os
    out = []
    today = _dt.date.today().isoformat()
    for a in getattr(job, "artifacts", []) or []:
        raw = os.path.expanduser(str(a.path).replace("{today}", today))
        try:
            lines = Path(raw).read_text(errors="replace").rstrip().splitlines()[-n:]
        except OSError:
            continue
        if lines:
            out.append(f"{raw} (last {len(lines)} lines):\n" + "\n".join(f"    {ln[:300]}" for ln in lines))
    return "\n".join(out)


def _servers(roster: Path | None) -> dict:
    import yaml

    try:
        doc = yaml.safe_load((roster or ROSTER).read_text()) or {}
    except Exception:  # noqa: BLE001 -- an unreadable roster is "unknown", not a crash
        return {}
    return doc.get("servers") or {}


def _machine_name(host: str, entry: dict) -> str:
    """The name the jobs manifest uses for a roster host (`manifest_machine`,
    e.g. winston -> box), else the host's own name."""
    return (entry.get("manifest_machine") if isinstance(entry, dict) else None) or host


def _entry_for_machine(machine: str, roster: Path | None) -> dict:
    for host, entry in _servers(roster).items():
        if isinstance(entry, dict) and machine in (host, entry.get("manifest_machine")):
            return entry
    return {}


def host_of(actor: str, roster: Path | None = None) -> str | None:
    """The MANIFEST machine name of the host whose ledger_actors include
    `actor` -- comparable to job.machine -- or None if unknown."""
    for host, entry in _servers(roster).items():
        if isinstance(entry, dict) and actor in (entry.get("ledger_actors") or []):
            return _machine_name(host, entry)
    return None


def actor_of(machine: str, roster: Path | None = None) -> str | None:
    """The principal that acts for a manifest machine (roster `access.actor`), or None."""
    entry = _entry_for_machine(machine, roster)
    actor = ((entry.get("access") or {}).get("actor")) or ((entry.get("ledger_actors") or [None])[0])
    return actor or None


def repo_for(job, root: Path) -> str | None:
    """OWNER/REPO of the repository the job's producer lives in, from its cmd.

    `.datacore/modules/<m>/...` is that module's own repository; anything else
    under `.datacore/` is the core. Read from the checkout's origin, because
    module remotes are not uniform (one module lives under a personal account).
    """
    import re

    cmd = str(getattr(job, "cmd", "") or "")
    m = re.search(r"\.datacore/modules/([A-Za-z0-9_-]+)/", cmd)
    path = root / ".datacore" / "modules" / m.group(1) if m else root
    try:
        url = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m2 = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$", url)
    return m2.group(1) if m2 else None


def delegate(job, failures: list[str], rec: dict, *, root: Path,
             assignee: str | None = None, dry: bool = False,
             roster: Path | None = None) -> tuple[str, str]:
    """Hand one failing job to `assignee`. Returns (state, detail).

    state: delegated | refused | exists | owner (a person's machine: a task, not a repair)
           | escalate (host-state fault, or the breaker is open: tell the owner, once)
           | paused (a usage limit: wait for it to reset; nobody is told again)

    REFUSED, BEFORE ANYTHING IS WRITTEN, when the repair cannot be checked
    where it would run. The done-condition is fix_check on `job.machine`'s
    artifacts, and it runs where the assignee's dispatcher runs. Miles runs on
    nightshift, so a box or mac job handed to him can only ever dead-letter
    after three attempts -- which is exactly what every delegation of a box
    or mac job did on 2026-09-21/22, and why the escalation report grew five
    entries, one of them the escalation job itself. "Fixes are not possible"
    is decidable up front here; saying so is what lets the operator be told
    now rather than after three failed attempts and a 48-hour window.
    """
    from actor_identity import this_actor
    from ledger.log import EventLog
    from ledger.policy import guarded_append, PolicyError

    if not getattr(job, "delegate", True):
        return "refused", f"{job.name} opts out of delegation (delegate: false); a person owns it"

    # EACH MACHINE'S OWN AGENT REPAIRS IT (owner, board D1, 2026-10-01: "each box
    # should have own repair agent"). Until then every repair went to the
    # operations agent on nightshift: a box or mac job became a pull request he
    # could not verify, and 0 of 122 repairs since 2026-09-21 succeeded -- the
    # refusal of b781d0e ("only the repairer's own host") was replaced the same
    # day by the two-stage PR path. The repairer is now the agent principal whose
    # `hosts` lists the failing machine (principals.yaml; no name ships here,
    # INS-3), so the repair runs where its check runs. A person's machine (the
    # owner's workstation) gets no automatic repair: job_verify files a task.
    if assignee is None:
        import roster as _roster
        person = _roster.person_on(job.machine)
        if person:
            return "owner", (f"{job.name} runs on {job.machine}, {person}'s own machine; no agent "
                             f"repairs it -- a task for {person}")
        assignee = _roster.resident_agent(job.machine)
        if not assignee:
            return "refused", (f"no agent principal lists {job.machine} under hosts in "
                               "principals.yaml; nobody to hand the repair to")

    manifest = LIB / "jobs" / "manifest.yaml"
    sha = contract_sha(job.name, manifest)
    if not sha:
        return "refused", f"{job.name} is not in the manifest"

    # CLASSIFY, THEN ASK THE BREAKER, BEFORE ANYTHING IS FILED (W1).
    fp = fingerprint(job, root, manifest)
    klass = classify_failure("\n".join(failures), job)
    if klass == HOST_STATE:
        return "escalate", (f"{job.name} on {job.machine} is failing on the HOST's state, which no "
                            f"agent repair in a fresh checkout can change; it needs a person (the "
                            f"owner) on {job.machine}")
    gate = breaker(root, job.name, fp)
    if gate["usage_pause"]:
        return "paused", (f"repairs are paused: the last repair hit the model usage limit; nothing "
                          f"is filed until it resets (about {USAGE_PAUSE_H}h)")
    if gate["open"]:
        return "escalate", (f"{job.name}: {gate['attempts']} repair attempts have failed since it last "
                            f"passed; no new repair until its contract or code changes "
                            f"(fingerprint {fp}) or it passes again -- it needs the owner")

    space = _space(root)
    actor = this_actor()
    iid = f"{MARK}-{job.name}-{time.strftime('%Y%m%d', time.gmtime())}"
    title = f"Repair {job.name}: failing on {job.machine} since {rec.get('first_failed')}"
    # ONE CREATE PER JOB PER DAY. The id already bounds the item; this bounds
    # the EVENT. Re-appending item.create for an id the ledger holds changes
    # nothing in the fold but still counts against the writer's daily creation
    # allowance -- on 2026-09-27..30 winston spent 30 of its 50 a day on repeats
    # of four ids, and every new repair after that was refused.
    try:
        # The space's authoritative state: an inadmissible row in a hub space is
        # not an existing repair (Codex round-3 verdict, autofix_reader).
        from ledger.hub import authoritative_state
        if (space / ".datacore" / "events").is_dir() and iid in authoritative_state(space).items:
            return "exists", f"{iid} is already in the ledger"
    except Exception:  # noqa: BLE001 -- an unreadable ledger falls through to the old path
        pass
    verify = (f"python3 {FIX_CHECK} --job {job.name} "
              f"--machine {job.machine} --contract-sha {sha}")

    # ONE STAGE OR TWO. The repairer can judge its own work only where the
    # artifacts are. On its own host: fix, then the ordinary verification is
    # the check. Anywhere else (decided 2026-09-22): the repairer fixes the
    # producer in its repository and OPENS A PULL REQUEST -- it never merges
    # (owner, 2026-09-25) -- and the check is that pull request, merged by the
    # owner. Then, on a resident, the
    # host's own principal pulls and runs the verification as a follow-up item
    # created when the first completes; on a visitor (the mac) nothing follows,
    # because the join protocol pulls on wake and the contract passes by itself.
    host = host_of(assignee, roster)
    then = None
    if host and host != job.machine:
        repo = repo_for(job, root)
        if not repo:
            return "refused", f"cannot name the repository {job.name}'s producer lives in"
        if repo not in PR_REPOS:
            return "refused", (f"{job.name}'s producer lives in {repo}; an agent may open a repair PR only in "
                               f"{', '.join(sorted(PR_REPOS))} (owner's boundary, 2026-09-22); "
                               f"a person owns it")
        check = (f"python3 {FIX_CHECK} --stage merged --job {job.name} "
                 f"--machine {job.machine} --contract-sha {sha} --item {iid} --repo {repo}")
        follower = actor_of(job.machine, roster)
        # No pull-and-verify follow-up: nothing is merged until the owner merges, and the
        # morning repair re-check verifies the host after that (owner, 2026-09-25).
        if False and follower and follower != assignee and not _is_visitor(job.machine, roster):
            then = {"id": f"{iid}-verify", "assignee": follower, "route": "dev",
                    "title": f"Pull and verify {job.name} on {job.machine} after {iid}",
                    "check": verify, "autofix": True, "job": job.name, "machine": job.machine,
                    "contract_sha": sha, "stage": "verify",
                    "body": (f"{assignee} merged a repair for {job.name} ({iid}). Bring this "
                             f"host up to date: `git -C ~/Data pull --ff-only` (and the module "
                             f"repository under .datacore/modules if the job lives there), then "
                             f"stop. The check runs the job's own verification; do not touch "
                             f"the contract.")}
        body = repair_body(job, failures, rec, stage="merged", repo=repo, item_id=iid,
                           follower=follower if then else None)
    else:
        check = verify
        body = repair_body(job, failures, rec)

    if dry:
        return "delegated", f"would delegate {iid} to {assignee}" + (f" then {then['assignee']}" if then else "")

    payload = {"id": iid, "title": title, "assignee": assignee,
               "check": check, "body": body, "requested_by": actor,
               "autofix": True, "job": job.name, "machine": job.machine,
               "contract_sha": sha, "fingerprint": fp, "failure_class": klass,
               "stage": "merged" if then or check != verify else "verify",
               # Repairing a producer is engineering, not research. The
               # title heuristics cannot know that and inferred the
               # default route, which framed the executing agent as a
               # literature reviewer and handed it a broken cron job.
               "route": "dev"}
    if then:
        payload["then"] = then
    try:
        guarded_append(EventLog(space, actor), "item.create", payload)
    except PolicyError as exc:
        return "refused", f"the gate refused the repair item: {exc}"
    except Exception as exc:  # noqa: BLE001 -- delegation must not break verification
        return "refused", f"{type(exc).__name__}: {exc}"
    return "delegated", f"{iid} -> {assignee}"


def _is_visitor(machine: str, roster: Path | None = None) -> bool:
    """A host whose presence is not promised: kind workstation, not always_on."""
    entry = _entry_for_machine(machine, roster)
    if not entry:
        return False
    return entry.get("kind") == "workstation" and not entry.get("always_on", False)


def repair_body(job, failures: list[str], rec: dict, *, stage: str = "verify",
                repo: str | None = None, item_id: str = "", follower: str | None = None) -> str:
    """What the repairing agent is actually told.

    Separate and pure so the boundary wording is testable. An agent that learns
    the rule by being REFUSED has already spent one of its three attempts
    discovering what it was not allowed to do; the instruction has to say it up
    front, and something has to keep it saying it.
    """
    return "\n".join([
        f"{job.name} has failed {rec.get('consecutive')} consecutive verification runs.",
        "",
        "Failures this run:",
        *[f"  - {f}" for f in failures[:6]],
        "",
        *(["The job's own last output (the real error):", tail, ""] if (tail := _artifact_tail(job)) else []),
        f"Producer: {getattr(job, 'cmd', '') or 'see manifest'}",
        f"Schedule: {getattr(job, 'schedule', '') or 'see manifest'}",
        "",
        "FIX THE PRODUCER, NOT THE CHECK.",
        "The check below re-reads this job's contract and refuses if it changed:",
        "loosening the regex, widening exit_ok, raising max_age_hours or removing",
        "the job all make verification pass and leave the fleet worse. If the",
        "contract is genuinely wrong, say so and stop -- that is a human's call,",
        "because the contract is what decides whether this job is healthy.",
        *([] if stage != "merged" else [
            "",
            f"THIS JOB RUNS ON {job.machine}, NOT HERE. You cannot verify it from this host,",
            f"so your part is a pull request in {repo} whose title or body contains",
            f"`{item_id}`. Fix the producer there, open the PR with that id in the title, and",
            "STOP -- do not merge; the owner merges (owner, 2026-09-25). The open PR is",
            "'waiting for the owner', not done: the check passes only once it is merged and",
            f"{job.name} verifies again (NS-10), and it refuses a PR that touched the jobs manifest.",
            (f"When it is merged, {follower} pulls on {job.machine} and runs the job's own "
             "verification as a follow-up item; you do not need to do that part.")
            if follower else
            (f"{job.machine} pulls on its own schedule and the contract will pass by itself; "
             "nothing follows for you."),
        ]),
    ])


def repairs(root: Path) -> list[dict]:
    """Every repair item and what became of it.

    Read from the ledger rather than a side file: the delegation happened in the
    ledger, and a second record of it would be a second thing to keep true.

    `closed_kind` is the field that matters. item.dismiss is overloaded -- org
    ingestion emits it when a human marks a task done, and the dead-letter emits
    it when an agent gave up after three attempts. Same event, opposite
    meanings; "dropped" is the one that means the operator now has to look.
    """
    from ledger.fold import fold

    space = _space(root)
    try:
        # Admitted events and their fold (Codex re-audit finding 2; round-3
        # verdict): the claim/release scan below reads the same events as the
        # state, so a forged release in a hub space is no "waiting for you".
        from ledger.hub import authoritative_events
        from ledger.finality import effective_events
        events, kw = authoritative_events(space)
        state = fold(events, **kw)
        # The scan below reads events with effect only (round 8): a release the
        # hub kept as a rejected proposal is no "waiting for you".
        events = effective_events(events, kw)
    except Exception:  # noqa: BLE001 -- an unreadable ledger is not an escalation
        return []
    # WAITING FOR YOU (NS-9): the repairer finished and released the item as
    # `waiting_on_owner` (its check: the pull request is open, the owner
    # merges). The latest claim/release decides; a later claim ends the wait.
    last_turn: dict[str, str] = {}
    for ev in events:
        iid = (ev.payload or {}).get("id")
        if iid and ev.type == "item.claim":
            last_turn[iid] = "claim"
        elif iid and ev.type == "item.release":
            last_turn[iid] = (ev.payload or {}).get("kind") or "release"
    out = []
    for iid, item in sorted(state.items.items()):
        payload = item.payload or {}
        if not (payload.get("autofix") or iid.startswith(MARK)):
            continue
        out.append({
            "id": iid,
            "status": item.status,
            "waiting_on_owner": (item.status == "created"
                                 and last_turn.get(iid) == "waiting_on_owner"),
            "closed_kind": item.closed_kind,
            "closed_reason": item.closed_reason,
            "job": payload.get("job", ""),
            "stage": payload.get("stage", ""),
            "assignee": payload.get("assignee", ""),
            "owner": item.owner,
            "closed_at": item.closed_at,
            # Each history line begins with the HLC of its event, so the first
            # one dates the item's creation.
            "opened_at": (item.history[0].split(" ", 1)[0] if item.history else None),
        })
    return out


def escalations(root: Path, *, window_h: float = ESCALATE_H * 2,
                now_ms: float | None = None) -> list[str]:
    """Repairs the operator genuinely has to hear about, and nothing else.

    The conditions the delegation path cannot resolve by itself: given up on, or
    closed without being verified. A repair in flight is not one of them.

    WINDOWED, and that is not a detail. A dismissal cannot be undone -- the
    ledger is append-only and a second item.dismiss on a closed item changes
    nothing -- so an unwindowed reading of "was it dropped" reports the same
    dead-letter every hour for the rest of the installation's life. The first
    version of this function did exactly that, and it would have replaced one
    permanent daily alert with a permanent hourly one: the precise failure being
    designed away.

    A dead-letter stops being news once it is older than the window. That is
    safe because it is not the record of the problem -- the JOB is. If the job
    is still failing, job_verify files a fresh repair with today's date and this
    escalates again on its own; if it recovered, there is nothing to say.
    """
    now = now_ms if now_ms is not None else time.time() * 1000.0
    acked = _acked()
    out = []
    for r in repairs(root):
        if r["id"] in acked:
            continue
        if r.get("waiting_on_owner"):
            # Done by the repairer, waiting on the owner's merge: neither
            # given up nor abandoned. The morning briefing lists the pull
            # request to review; paging for it would be the wrong message.
            continue
        if r["status"] in ("created", "claimed"):
            # THE THIRD CONDITION, which this module's docstring promised from
            # the start and the first version never implemented. A repair
            # nobody claims is not dismissed, so it matched nothing above and
            # would have sat in `created` forever: the failure withheld from
            # the operator, handed to nobody, and reported by nothing. That is
            # delegation as a way of losing failures, the exact outcome the
            # escalation job exists to prevent.
            age = _age_h(r.get("opened_at"), now)
            if age is not None and age > ESCALATE_H:
                out.append(f"{r['job']}: repair {r['status']} for {age:.0f}h and not "
                           f"finished — nobody is completing it")
            continue
        if r["status"] != "dismissed":
            continue
        # DONE ON A PULL REQUEST IS NOT FIXED (NS-10). A repair whose done-check was the
        # job's own verification finished when that check passed. One closed on the
        # evidence of a pull request -- stage "merged", which until 2026-09-26 passed on an
        # open PR alone -- is fixed only once the job verifies again, so it goes through
        # the same recovered_since test as a dead-letter below.
        on_pr = r.get("stage") == "merged" or "pull request" in (r.get("closed_reason") or "")
        if r["closed_kind"] == "housekeeping" or (r["closed_kind"] == "done" and not on_pr):
            continue
        age_h = _age_h(r["closed_at"], now)
        if age_h is not None and age_h > window_h:
            continue
        if recovered_since(root, r["job"], r["closed_at"]):
            # The docstring above promised "if it recovered, there is nothing
            # to say" and nothing checked. A dead-letter kept the report red
            # for 48 hours after its job had passed (2026-09-22). The JOB is
            # the record of the problem; when the ledger shows it verified
            # after the drop, the drop is history.
            continue
        if r["closed_kind"] == "dropped":
            out.append(f"{r['job']}: {r['assignee']} gave up — "
                       f"{r['closed_reason'] or 'dead-lettered'}")
        elif r["closed_kind"] == "done":
            out.append(f"{r['job']}: repair closed on a pull request, but {r['job']} has not "
                       f"verified since — not fixed")
        else:
            out.append(f"{r['job']}: repair closed as {r['closed_kind'] or 'unknown'} "
                       f"without verifying")
    return out


#: Escalations the operator has seen. Local state, not a ledger event: "I read
#: this" is one person's decision about their own alerting, and the fleet's
#: shared history should not fill up with it.
ACK_FILE = Path.home() / ".datacore" / "state" / "autofix-acked.json"


def _acked() -> set[str]:
    try:
        return set(json.loads(ACK_FILE.read_text()).get("acked", []))
    except Exception:  # noqa: BLE001 -- a missing or broken ack file acks nothing
        return set()


def ack(item_id: str) -> None:
    """Stop escalating one repair the operator has dealt with.

    An escalation with no way to say "seen" pages until it ages out, which is
    how a person learns to ignore the channel. Acknowledging does NOT fix the
    job: if the underlying failure persists, job_verify files a fresh repair
    with a new id and it escalates again. This silences the notification, not
    the problem, and the distinction is why it is safe.
    """
    current = _acked()
    current.add(item_id)
    ACK_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    ACK_FILE.write_text(json.dumps(
        {"acked": sorted(current), "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        indent=2))


def recovered_since(root: Path, job: str, closed_hlc: str | None) -> bool:
    """Has `job` passed verification, on any host, since the repair was closed?

    Read from the system space's ledger, where every host's job_verify writes
    its result: the box sees nightshift's and the mac's passes without asking
    them. An unreadable close time is "not recovered" -- a thing that cannot
    be dated must not disappear.
    """
    try:
        closed_ms = float(str(closed_hlc).split(".")[0])
    except (ValueError, TypeError):
        return False
    events = _space(root) / ".datacore" / "events"
    for f in events.glob("*.jsonl") if events.is_dir() else []:
        try:
            for line in f.read_text(errors="replace").splitlines():
                if '"job.verify"' not in line or job not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                p = e.get("payload") or {}
                if p.get("metric") != "job.verify" or p.get("job") != job or not p.get("ok"):
                    continue
                if float(str(e.get("hlc", "0")).split(".")[0]) > closed_ms:
                    return True
        except OSError:
            continue
    return False


def _age_h(hlc: str | None, now_ms: float) -> float | None:
    """Hours since an HLC timestamp, or None if it cannot be read.

    None means "cannot tell", and a thing that cannot be dated is reported
    rather than silently aged out -- an unreadable timestamp must not become a
    way for an escalation to disappear.
    """
    if not hlc:
        return None
    try:
        return (now_ms - float(str(hlc).split(".")[0])) / 3600000.0
    except (ValueError, TypeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None)
    ap.add_argument("--list", action="store_true", help="show repair items and their state")
    ap.add_argument("--escalations", action="store_true",
                    help="print only what the operator has to act on; exit 1 if any")
    ap.add_argument("--ack", metavar="ITEM_ID",
                    help="stop escalating one repair you have dealt with "
                         "(silences the notification, not the problem: a job that "
                         "keeps failing gets a fresh repair and escalates again)")
    ap.add_argument("--reset", metavar="JOB",
                    help="reset JOB's repair breaker after you fixed its cause by hand "
                         "(needs --reason; recorded in the ledger)")
    ap.add_argument("--reason", default="")
    a = ap.parse_args()

    import os
    root = Path(a.root or os.environ.get("DATACORE_ROOT") or (Path.home() / "Data"))

    if a.reset:
        reset_breaker(root, a.reset, reason=a.reason)
        print(f"breaker reset for {a.reset}; it gets a fresh {BREAK_AFTER} repair attempts")
        return 0

    if a.ack:
        ack(a.ack)
        print(f"acknowledged {a.ack}; it will not escalate again")
        return 0

    if a.escalations:
        rows = escalations(root)
        for r in rows:
            print(f"  {r}")
        # Positively, so "the check ran and found nothing" cannot be mistaken
        # for "the check did not run" -- DIP-0046 §8.
        print(f"autofix: {len(rows)} repair(s) need a person")
        return 1 if rows else 0

    if a.list:
        rows = repairs(root)
        for r in rows:
            mark = r["status"] if r["status"] != "dismissed" else f"dismissed/{r['closed_kind']}"
            if r.get("waiting_on_owner"):
                mark = "waiting for you"
            print(f"  {mark:<18} {r['id']:<44} {r['job']} -> {r['assignee']}")
        print(f"\nautofix: {len(rows)} repair item(s)")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
