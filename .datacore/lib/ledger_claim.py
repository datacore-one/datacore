#!/usr/bin/env python3
"""Claim -> route -> execute -> complete. The consumer the action loop lacked.

Renamed from `ledger_dispatch` on 2026-08-15. "Dispatch" reads as sending work
OUT, which is the one thing this does not do -- creation lives in
`materialize()`, publication in `ledger_transport.converge`. This process only
ever pulls: it folds the log, claims what is addressed to it, runs it, and
records the result. Calling that dispatch cost real confusion about which half
of the loop was broken.

DIP-0038 built `materialize()` (proposals become ledger items) and `act()`
(items move between states). `briefing_materialize.py` then supplied the caller
for the first half. Nothing supplied the second: no process ever READ the
ledger and did the work, so every delegated item sat at status `created`
forever. Winston could propose; nobody could pick up.

This is that consumer, and deliberately the smallest one that closes the loop:

    fold the log -> take unclaimed items -> classify a route -> item.claim
    -> run it -> item.complete (or item.release on failure)

Three properties are the point, and each is checkable in the log afterwards:

  THE CLAIM IS RECORDED BEFORE THE WORK. `item.claim` is appended first, so a
  crash mid-execution leaves a claimed-but-incomplete item -- visible, and
  recoverable -- rather than work that happened with no trace that anyone
  started it.

  THE ROUTE IS RECORDED, NOT INFERRED LATER. Which agent family picked an item
  up is written into the claim payload. "The right agent picked it up" is then
  a question you answer by reading the ledger, not by trusting a log line.

  SIDE EFFECTS ARE NOT AUTO-RUN. An item carrying an `effects` tag is refused
  here even though it exists: `guarded_append` gates CREATION against a
  recorded grant, and creation-time gating says nothing about whether running
  it now, unattended, is wanted. Execution requires --execute; without it this
  plans and writes nothing.

Usage:
    ledger_claim.py --space DIR [--actor NAME] [--limit N] [--execute]
"""
from __future__ import annotations

import argparse
import os
import subprocess
from process_run import run as run_process
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from briefing.actions import act  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.policy import guarded_append, PolicyError, approval_payload_hash
from executors import get_executor  # noqa: E402
from ops_markers import AUTH_FAILURE_MARKERS  # noqa: E402

CLAIMABLE = "created"
# Three strikes. Enough for a transient network or rate-limit blip to clear,
# few enough that a genuinely unsatisfiable item stops within one hour of a
# 15-minute timer instead of running for days.
MAX_ATTEMPTS = 3
TIMEOUT = 600

# Phrases that mean the agent declined or was prevented, in a run that exits 0
# with a long, confident-looking explanation. Checked against the START of the
# output only: a task ABOUT blocked work ("summarise why the deploy is
# blocked") legitimately contains these words further down.
REFUSAL_MARKERS = (
    "i can't complete",
    "i cannot complete",
    "i'm unable to",
    "i am unable to",
    "cannot proceed",
    "what's blocking it",
    "blocks every tool call",
)

# Route -> how the executing agent should be framed. Kept deliberately thin:
# the nightshift classifier already owns route SELECTION, and duplicating its
# taxonomy here is how two routers drift into disagreeing about the same task.
ROUTE_FRAMING = {
    "dev": "You are a careful software engineer. Investigate and report.",
    "research": "You are a research assistant. Find and summarise what is known.",
    "strategic": "You are a project manager. Assess status, blockers and next step.",
    "trading": "You are a trading analyst. Report position and risk facts only.",
}
DEFAULT_ROUTE = "research"


def classify_route(text: str) -> tuple[str, str]:
    """(route, reason). Reuses the nightshift classifier's heuristics when it
    is importable, so there is ONE routing taxonomy rather than a second one
    invented here. Falls back to the default route if the module is absent --
    a missing classifier must not stop the loop, only make it less specific."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules" / "nightshift" / "lib"))
        from classifier import HEURISTIC_PATTERNS  # type: ignore
    except Exception:  # noqa: BLE001
        return DEFAULT_ROUTE, "classifier unavailable; default route"
    for pattern, route, why in HEURISTIC_PATTERNS:
        if pattern.search(text):
            return route, f"heuristic: {why}"
    return DEFAULT_ROUTE, "no heuristic matched; default route"



def run_task(title: str, route: str, cwd: Path, item_id: str = "", hops: int = 0, actor: str = "") -> tuple[bool, str, dict]:
    """Execute one item. Returns (ok, detail).

    Runs through the executors registry (.datacore/lib/executors) rather than
    shelling out to a runtime this module picks itself. An earlier version of
    this file probed for `openclaw`/`hermes`/`claude` binaries by hand, which
    duplicated an abstraction that already existed AND skipped what it gives
    you: per-adapter cost accounting, schema contracts, and a `run()` that
    never raises. Which adapter runs is configuration ($DATACORE_EXECUTOR),
    not a guess made here -- so a machine with two runtimes installed uses the
    one its operator chose instead of whichever the probe happened to try first.
    """
    framing = ROUTE_FRAMING.get(route, ROUTE_FRAMING[DEFAULT_ROUTE])
    prompt = f"{framing}\n\nTask: {title}\n\nBe brief. Report what you found. If you change files, commit only your task changes; preserve unrelated staged and working-tree changes."

    import time as _time
    meta: dict = {}
    try:
        ex = get_executor()
    except ValueError as exc:               # unknown $DATACORE_EXECUTOR
        return False, str(exc), meta

    # cwd is passed, not assumed. The agent must work in the space it was
    # dispatched for -- `acceptEdits` is scoped to the working directory, so a
    # missing cwd turns every write into a denied out-of-scope write. The
    # guard-bypass env is set by the adapter that spawns the process, rather
    # than by mutating this process's os.environ and hoping it is inherited.
    started = _time.monotonic()
    # space and item travel with the call so the spend event lands in a log
    # that gets folded, under the declared actor, linked to what incurred it.
    res = ex.run(prompt, timeout_s=TIMEOUT, cwd=cwd, space=cwd, item=item_id, actor=actor or None)
    # Recorded for EVERY outcome, not just success: a failure that cost real
    # money is exactly the one worth being able to add up later.
    meta = {"executor": ex.name,
            "model": res.model,
            "cost_cents": res.cost_cents,
            "duration_s": round(_time.monotonic() - started, 1)}
    if res.error:
        return False, f"{ex.name}: {res.error[:250]}", meta

    out = (res.text or "").strip()
    if not out:
        return False, f"{ex.name} returned no output", meta

    combined = out.lower()[:400]
    marker = next((m for m in AUTH_FAILURE_MARKERS if m in combined), None)
    if marker:
        return False, f"{ex.name} auth rejected ({marker!r}): {out[:200]}", meta
    return True, out, meta


def _journal(space: Path, actor: str, lines: list[str]) -> None:
    """Append this run's outcomes to the space journal. Never raises.

    WHY THE WORKER WRITES THIS AND NOT THE AGENT.

    After a 15-item unattended run, Data had journal entries and Miles and Tris
    had none -- because Data's runtime happens to journal on its own and the
    other two do not. Ten completed tasks left no narrative trace at all, so a
    human reading the journal would conclude nothing happened on two of three
    hosts while the ledger proved otherwise.

    Asking each agent to journal in its prompt was the obvious fix and is the
    wrong one: it makes the record depend on the agent's own account, and this
    system already learned that agent prose is not evidence -- `_isolated_check`
    exists because two attempts at trusting self-reports passed failures as
    DONE. A record that can be forgotten or embellished is not a record.

    So this is DERIVED bookkeeping: written from events already committed,
    identical in form for every agent and every runtime, and impossible to
    fabricate because nothing here is reported -- it is all read back from what
    just happened. The reflective layer (what was learned) is a separate
    concern and deliberately not attempted here.
    """
    if not lines:
        return
    try:
        import datetime
        day = datetime.date.today().isoformat()
        jdir = space / "journal"
        if not jdir.is_dir():                     # some spaces use notes/journals
            alt = space / "notes" / "journals"
            jdir = alt if alt.is_dir() else jdir
        jdir.mkdir(parents=True, exist_ok=True)
        path = jdir / f"{day}.md"
        stamp = datetime.datetime.now().strftime("%H:%M")
        body = [f"\n## {actor} — {stamp} — ledger claim run\n"]
        body += [f"- {ln}" for ln in lines]
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(body) + "\n")
        # AND COMMIT IT, because this function writes into the very tree
        # `_artifact_tree_clean` insists is clean. An uncommitted journal is a
        # dirty path that is neither a ledger append nor the agent's artifact,
        # so the NEXT run in this space fails every check closed -- "commit task
        # changes before artifact verification" -- about a file the dispatcher
        # itself left there. Until an hourly converge autosaved it, a space
        # could not complete a delegated item twice in a row.
        #
        # Only this file is staged. `git add -A` here would sweep up whatever
        # the agent left behind and commit it as though it had been verified,
        # which is the opposite of what the check exists for.
        subprocess.run(["git", "-C", str(space), "add", "--", str(path)],
                       capture_output=True, timeout=60)
        subprocess.run(["git", "-C", str(space), "commit", "-q", "-m",
                        f"journal: {actor} ledger claim run", "--", str(path)],
                       capture_output=True, timeout=60)
    except Exception:  # noqa: BLE001 -- a journal failure must not fail the work
        pass


#: Failure text that means "this host could not run ANYTHING", as opposed to
#: "this task did not work". The distinction decides whether an item counts
#: down towards being dead-lettered, so it is kept narrow and literal: every
#: marker below is produced by the executor layer itself, never by a task.
INFRASTRUCTURE_MARKERS = (
    "binary not found",
    "not installed",
    "timed out after",
    "unknown executor",
    "auth rejected",
    "returned no output",
    "returned an invalid result",
    "execution failed (exit",
)


def _infrastructure_failure(detail: str) -> bool:
    low = (detail or "").lower()
    return any(marker in low for marker in INFRASTRUCTURE_MARKERS)


def _commit_result(space: Path, item_id: str) -> tuple[bool, str]:
    """Record whatever this execution produced, so the check has something to read.

    COMPLETION USED TO DEPEND ON THE AGENT REMEMBERING TO COMMIT. The prompt
    asks it to; `_isolated_check` requires it, because a pass must mean
    something durable anyone can verify later from the sha. Measured 2026-09-18
    across two hosts and two runtimes: the agents computed the right answer
    (216, exactly correct) every time and committed it none of the time, so
    every delegated item failed a check its work had actually satisfied. A
    mechanical step that decides whether correct work counts is not something
    to leave to model compliance.

    This does not weaken the guarantee, because COMMITTING IS NOT VERIFYING.
    The check still runs afterwards, in an isolated worktree, and it still
    decides. What changes is only that the evidence exists to be checked.

    Attribution is what makes it safe: the dispatcher refuses to start an item
    at all unless the tree is clean apart from ledger appends, so anything dirty
    at this point was produced by THIS execution. That is why the sweep can be
    broad without being reckless -- there is nothing else here to sweep up.

    Ledger logs are left alone. They are appends, `_artifact_tree_clean`
    already excludes them, and the transport publishes them on its own terms.
    """
    rc, staged, _ = _git_out(space, "status", "--porcelain")
    if rc != 0:
        return False, "the working tree could not be read"
    paths = []
    for line in staged.splitlines():
        name = line[3:].strip().strip('"')
        if not name or "/.datacore/events/" in f"/{name}" or name.startswith(".datacore/events/"):
            continue
        paths.append(name)
    if not paths:
        return True, ""                       # the agent committed, or produced nothing
    rc, _, err = _git_out(space, "add", "--", *paths)
    if rc != 0:
        return False, f"git add refused: {err.strip()[:200]}"
    rc, _, err = _git_out(space, "commit", "-q", "-m",
                          f"result: {item_id}", "--", *paths)
    if rc != 0:
        # A pre-commit hook refusing (DIP-0015 structure, a boundary scan) is
        # the interesting case and the one that reads as a mystery otherwise:
        # the artifact stays uncommitted, the check fails closed, and the
        # message blames the agent. Say what actually refused.
        _git_out(space, "reset", "-q", "HEAD", "--", *paths)
        return False, f"commit refused: {err.strip()[-300:]}"
    return True, ""


def _git_out(space: Path, *args: str) -> tuple[int, str, str]:
    p = subprocess.run(["git", "-C", str(space), *args],
                       capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout or "", p.stderr or ""


def _artifact_tree_clean(space, offenders: list | None = None):
    """Only ledger append records may differ from the checked commit.

    `offenders`, when given, is filled with the paths that made this False, so
    the caller can NAME them. It said only "commit task changes", which points
    at the agent -- and on 2026-09-18 the real cause was a DIP-0015 structure
    hook refusing a top-level `drill/` directory, so the agent's commit could
    never have succeeded and no amount of retrying would have helped. A refusal
    that describes the wrong cause costs more than one that says nothing.
    """
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=space, capture_output=True, text=True)
    if root.returncode:
        return False
    root_path = Path(root.stdout.strip()).resolve()
    try:
        event_prefix = (Path(space).resolve().relative_to(root_path) / ".datacore/events").as_posix() + "/"
    except ValueError:
        return False
    paths = []
    for command in (["git", "diff", "--name-only", "-z", "HEAD"],
                    ["git", "ls-files", "--others", "--exclude-standard", "-z"]):
        result = subprocess.run(command, cwd=root_path, capture_output=True)
        if result.returncode:
            return False
        paths.extend(os.fsdecode(name) for name in result.stdout.split(b"\0") if name)
    def permitted(name: str) -> bool:
        return (name.startswith(event_prefix) and "/" not in name[len(event_prefix):]
                and (name.endswith(".jsonl") or name.endswith(".lock")))
    bad = [name for name in paths if not permitted(name)]
    if offenders is not None:
        offenders.extend(bad)
    return not bad


def _isolated_check(space: Path, check: str) -> tuple[bool, str]:
    """Check a fresh worktree of the committed result, not the agent's directory.

    What this DOES buy, and it is worth having:

      The artifact must be COMMITTED to be checkable, so a pass means something
      durable that anyone can verify later from the sha — not a fact about one
      machine's /tmp that disappears on reboot. This is what makes the
      `artifact_commit` sequencing meaningful.

      Checks cannot quietly depend on machine-local state.

    What it does NOT buy, stated plainly because an earlier version of this
    claimed otherwise: **it does not prevent a fabricated artifact.** Measured —
    an "agent" that writes `faked` into proof.txt without doing the work still
    passes `test -s proof.txt` here, because committing the agent's output is
    precisely what isolation does, and for a file-producing task producing the
    file IS the work.

    The defence against fabrication is CHECK STRENGTH, not isolation:

        test -s proof.txt              passes on "faked"   <- asserts existence
        grep -qx verified proof.txt    fails on "faked"    <- asserts content

    So a check must assert the OUTCOME, never merely that something appeared. A
    check that only tests existence is a check an agent satisfies by touching a
    file, and no amount of sandboxing repairs that.

    A worktree that cannot be created fails CLOSED. An isolation mechanism that
    silently degrades to the unisolated path is worse than none: it reports the
    same green.
    """
    import tempfile
    dirty: list[str] = []
    if not _artifact_tree_clean(space, dirty):
        print("         -> check FAILED CLOSED: commit task changes before artifact verification; "
              "existing index and files preserved"
              + (f" (uncommitted: {', '.join(sorted(dirty)[:4])})" if dirty else ""))
        return False, ""
    rc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=space,
                        capture_output=True, text=True)
    if rc.returncode != 0:
        print("         -> check FAILED CLOSED: cannot resolve HEAD for isolation")
        return False, ""
    head = rc.stdout.strip()
    with tempfile.TemporaryDirectory(prefix="check-") as tmp:
        wt = Path(tmp) / "verify"
        add = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "worktree", "add", "--detach", str(wt), head],
                             cwd=space, capture_output=True, text=True)
        if add.returncode != 0:
            print(f"         -> check FAILED CLOSED: no isolated worktree "
                  f"({(add.stderr or '').strip()[:90]})")
            return False, head
        try:
            # THE CHECK'S OWN STDERR IS THE DIAGNOSIS, and it was thrown away.
            # A check runs against the COMMITTED tree, so every input it reads
            # must be committed too -- and in a Phase 1 space `next_actions.org`
            # is generated and gitignored, so a check that reads it fails with
            # "No such file or directory" no matter what the agent does.
            # Reported as "check failed" alone, that is indistinguishable from
            # a wrong answer, and it cost a full live round to tell apart.
            proc = run_process(check, shell=True, cwd=str(wt),
                               capture_output=True, timeout=120)
            ok = proc.returncode == 0
            if not ok:
                # Decoded defensively: without `text=True` these are BYTES, and
                # the first version of this concatenated them as str and raised
                # TypeError mid-dispatch -- leaving the item claimed with no
                # completion, which is the one state this file works hardest to
                # avoid. A diagnostic that can crash the run is worse than no
                # diagnostic.
                def _text(v):
                    if isinstance(v, bytes):
                        return v.decode("utf-8", "replace")
                    return v or ""
                why = (_text(proc.stderr) + _text(proc.stdout)).strip().splitlines()
                if why:
                    print(f"         -> the check said: {why[-1][:180]}")
            current = subprocess.run(["git", "rev-parse", "HEAD"], cwd=space, capture_output=True, text=True)
            return ok and current.returncode == 0 and current.stdout.strip() == head and _artifact_tree_clean(space), head
        except subprocess.TimeoutExpired:
            return False, head
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                           cwd=space, capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True, type=Path)
    from actor_identity import this_actor
    ap.add_argument("--actor", default=this_actor())
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--execute", action="store_true",
                    help="actually claim and run; without it, plan only and write nothing")
    args = ap.parse_args()

    space = args.space.resolve()
    events = read_events(space)
    state = fold(events)
    claimable = [i for i in state.items.values() if i.status == CLAIMABLE]

    # A genesis-imported GTD task is a MIRROR of an org heading, not a work
    # queue. Both land at status `created`, so before this filter a single
    # space offered 343 "claimable items" -- the whole backlog -- and a
    # scheduled dispatcher would have started working through the principal's
    # personal todo list unattended. Delegation is opt-in: only items that were
    # materialized as delegations are dispatchable. The discriminator is the
    # payload's `org` block, which genesis writes (heading, level, filetags,
    # parent) and materialize() never does.
    pending = [i for i in claimable if not (i.payload or {}).get("org")]
    mirrored = len(claimable) - len(pending)

    # ADDRESSED WORK GOES TO ITS ADDRESSEE. `item.claim` is an append, not a
    # lock: every dispatcher folds its OWN copy of the log, so a claim written
    # on one machine does not exist on another until it converges. Two
    # dispatchers watching one space therefore both see `created` and both
    # claim legitimately -- item 929eb69d6b was claimed AND completed by both
    # winston and miles, two models, two costs, one task. The answers happened
    # to agree, which is the hardest kind of duplication to notice.
    #
    # An `assignee` in the payload makes the race impossible instead of
    # unlikely: a dispatcher simply declines what is addressed to someone else.
    # This is cheaper and stricter than a claim-lease, which would still be
    # racy across an eventually-consistent log.
    #
    # WHO COUNTS AS THE ADDRESSEE is `actor_identity.addressed_to`, the same
    # function the policy gate asks, because this filter used to compare the
    # two names as strings and a principal here has more than one writer name.
    # See that function for what the string compare cost.
    from actor_identity import addressed_to
    addressed = [i for i in pending
                 if not addressed_to(args.actor, (i.payload or {}).get("assignee"))]
    pending = [i for i in pending if i not in addressed]

    # AND AN ITEM ADDRESSED TO NOBODY IS NOT DISPATCHED AT ALL. This used to
    # stay open to whoever got there first, kept because changing it would
    # change the meaning of work already in flight -- but first-come is the
    # race above, not a mitigation of it, and the 2026-09-18 drill reproduced
    # it: both hosts claim, both run the model, the loser's `item.complete`
    # folds to "no-op (already claimed)" and its answer is discarded. Two
    # costs, one result, no error anywhere.
    #
    # Refused rather than auto-addressed: choosing who does the work is the
    # delegator's call, and a dispatcher that picked for itself would be
    # answering the question the assignee field exists to ask. Counted and
    # named in the summary so an unaddressed item is a visible queue instead
    # of the silent skip this filter would otherwise be. Measured before the
    # change: no space had one.
    unaddressed = [i for i in pending if not (i.payload or {}).get("assignee")]
    pending = [i for i in pending if i not in unaddressed]

    # GIVE UP AFTER MAX_ATTEMPTS. `item.release` returns an item to `created`,
    # which is claimable, so a 15-minute timer re-claims it forever: item
    # 4e6e2c5be4521870 was released 40 times, and winston released one item 38
    # times over 48.9h on a dead OAuth token. Nothing counted, nothing backed
    # off, nothing gave up. One unsatisfiable item becomes a permanent
    # fleet-wide loop that also crowds out real work behind --limit.
    #
    # Counted from the log rather than a payload field, because the count must
    # survive across machines and processes -- two dispatchers on one space
    # (winston and miles both watch 2-datacore) share the item but share no
    # memory. The events are the only common ground.
    #
    # Exhausted items are DISMISSED, once, with the reason. Dismissal takes
    # them out of `created`, so they stop being claimable and become visible as
    # a decision in the log rather than disappearing behind a filter.
    attempts: dict[str, int] = {}
    for ev in events:
        if ev.type == "item.release":
            payload = ev.payload or {}
            iid = payload.get("id")
            # An infrastructure release is not an attempt at the TASK. Counting
            # it dismissed good items because a host's runtime was down rather
            # than because the work was unsatisfiable -- see the release path.
            if iid and payload.get("kind") != "infrastructure":
                attempts[iid] = attempts.get(iid, 0) + 1

    exhausted = [i for i in pending if attempts.get(i.id, 0) >= MAX_ATTEMPTS]
    for item in exhausted:
        n = attempts.get(item.id, 0)
        title = (item.payload or {}).get("title", item.id)[:60]
        # Dismissal is a WRITE, so it obeys --execute like every other write
        # here. The module contract is that a run without it plans and writes
        # nothing; a dead-letter that fired during a dry run would break that
        # for the one path an operator is most likely to run while diagnosing.
        if not args.execute:
            print(f"would deadletter  {title}  ({n} failed attempts)")
            continue
        EventLog(space, args.actor).append(
            "item.dismiss",
            {"id": item.id, "owner": args.actor, "kind": "dropped",
             "reason": f"gave up after {n} failed attempts"})
        print(f"DEADLETTER  {title}\n         -> {n} failed attempts; dismissed")
    pending = [i for i in pending if attempts.get(i.id, 0) < MAX_ATTEMPTS]
    pending.sort(key=lambda i: i.id)

    mirror_note = f" ({mirrored} org-mirrored task(s) skipped -- not delegations)" if mirrored else ""
    if addressed:
        mirror_note += f"; {len(addressed)} addressed to another agent"
    if unaddressed:
        mirror_note += (f"; {len(unaddressed)} addressed to NOBODY -- not dispatched, "
                        f"give each an assignee: "
                        + ", ".join((i.payload or {}).get("title", i.id)[:40] for i in unaddressed[:3]))
    if not pending:
        print(f"nothing to dispatch: no delegated items awaiting claim{mirror_note}")
        return 0

    print(f"{len(pending)} delegated item(s) awaiting claim; limit {args.limit}{mirror_note}")
    dispatched = failed = refused = review = 0

    journal_lines: list[str] = []
    for item in pending[:args.limit]:
        title = (item.payload or {}).get("title") or item.id
        effects = (item.payload or {}).get("effects") or []
        route, why = classify_route(title)

        if effects:
            # Gated at creation against a grant; that is not consent to run it
            # unattended right now. Refuse loudly rather than quietly skip.
            print(f"REFUSED  [{route}] {title[:70]} -- side effects {effects}, needs a human")
            refused += 1
            continue

        check = (item.payload or {}).get("check")

        if not args.execute:
            gate = f"check: {check}" if check else "NO CHECK -> cannot auto-complete"
            print(f"would claim [{route}] {title[:70]}  ({why}; {gate})")
            continue

        # A DIRTY TREE FAILS EVERY CHECK IN THIS RUN, SO STOP AT THE FIRST ONE.
        # `_artifact_tree_clean` looks at the whole space, not at one item's
        # artifact. So an item whose agent left work uncommitted does not just
        # fail itself -- it fails every item dispatched after it, each one
        # spending a full model call to produce a result that cannot be
        # verified. Observed 2026-09-18 on nightshift: two items, the second
        # failed on the first one's leftover, and both answers were correct.
        #
        # Reported as a stop with the offending paths rather than pressed on,
        # because the condition cannot clear itself mid-run and the cost of
        # continuing is real money for guaranteed failures.
        if check:
            dirty: list[str] = []
            if not _artifact_tree_clean(space, dirty):
                # An EMPTY list means the tree could not be read at all (not a
                # git repo, git unavailable) rather than that nothing is dirty.
                # Saying "-> " and nothing sends the reader looking for a file.
                where = (", ".join(sorted(dirty)[:5]) if dirty
                         else "the working tree could not be read (is this a git repository?)")
                print(f"STOPPING: the working tree carries uncommitted work, so every check "
                      f"in this run would fail closed\n         -> {where}"
                      f"\n         -> commit or discard it, then dispatch again")
                break

        # Claim BEFORE working, so an interrupted run is visible as claimed.
        from claim_gate import check_claim
        _ok, _why = check_claim(args.actor, item.payload or {}, space_dir=space)
        if not _ok:
            print(f"REFUSED  {(item.payload or {}).get('title', item.id)[:60]}\n         -> {_why}")
            continue

        try:
            guarded_append(EventLog(space, args.actor),
                "item.claim", {"id": item.id, "owner": args.actor, "route": route, "reason": why,
                               "payload_hash": approval_payload_hash(item.payload)})
        except PolicyError as exc:
            print(f"REFUSED  {title[:70]}: {exc}")
            refused += 1
            continue
        ok, detail, meta = run_task(title, route, space, item.id, actor=args.actor)
        if ok and check:
            committed, why_not = _commit_result(space, item.id)
            if not committed and why_not:
                print(f"         -> could not record the result: {why_not}")
            # The ONLY thing that completes an item. An agent that declined
            # produces fluent, confident prose and exits 0; two attempts at
            # sniffing that prose for refusal markers both passed a failure as
            # DONE, because the model rephrases ("I can't" / "I could not").
            # Prose is not evidence. A check that passes is.
            passed, sha = _isolated_check(space, check)
            if passed:
                act(space, item.id, "complete", args.actor, detail={
                    "owner": args.actor,
                    "route": route,
                    "executor": meta.get("executor"),
                    "model": meta.get("model"),
                    "cost_cents": meta.get("cost_cents"),
                    "duration_s": meta.get("duration_s"),
                    "check": check,
                    "artifact_commit": sha,
                })
                journal_lines.append(
                    f"DONE `{item.id[:12]}` {title[:70]} — {meta.get('executor','?')}"
                    f" / {meta.get('model') or 'model n/a'}, {meta.get('cost_cents','?')}c,"
                    f" {meta.get('duration_s','?')}s, artifact `{sha[:10]}`")
                print(f"DONE     [{route}] {title[:70]}\n         -> check passed "
                      f"@ {sha[:10]} ({meta.get('cost_cents', '?')}c, "
                      f"{meta.get('duration_s', '?')}s)")
                dispatched += 1
            else:
                EventLog(space, args.actor).append(
                    "item.release", {"id": item.id, "owner": args.actor,
                                     "artifact_commit": sha,
                                     "error": f"check failed: {check}"})
                journal_lines.append(
                    f"FAILED `{item.id[:12]}` {title[:60]} — check did not pass: `{check}`")
                print(f"FAILED   [{route}] {title[:70]}\n         -> check failed: {check}")
                failed += 1
            continue
        if ok:
            # Ran, but nothing can attest it did the job. Leave it CLAIMED and
            # record the output for a human -- never complete on trust.
            EventLog(space, args.actor).append(
                "item.verify", {"id": item.id, "owner": args.actor,
                                "needs_review": True, "output": detail[:1000]})
            print(f"REVIEW   [{route}] {title[:70]}\n         -> ran, no check to prove it; left claimed")
            review += 1
        else:
            # Release, not complete: an item that failed must return to the
            # pool rather than be recorded as finished work.
            #
            # AND SAY WHOSE FAULT IT WAS. `item.release` is what the dead-letter
            # counter counts, and after MAX_ATTEMPTS the item is dismissed for
            # good. That is right for a task nobody can satisfy and wrong for a
            # host whose agent runtime is down: plur-claw's openclaw stopped
            # answering on 2026-09-18, and every item addressed to `data` would
            # have been dismissed within three ticks -- deleting good work
            # because one machine was sick. An infrastructure release is marked
            # so the counter can skip it; the item stays available for the host
            # when it recovers.
            infra = _infrastructure_failure(detail)
            EventLog(space, args.actor).append(
                "item.release", {"id": item.id, "owner": args.actor, "error": detail[:300],
                                 **({"kind": "infrastructure"} if infra else {})})
            journal_lines.append(
                f"FAILED `{item.id[:12]}` {title[:60]} — {detail[:110]}")
            print(f"FAILED   [{route}] {title[:70]}\n         -> {detail[:150]}")
            failed += 1

    # One entry per run, not per item: the batch is the unit of work a reader
    # cares about, and fifteen separate headings would bury the journal.
    _journal(space, args.actor, journal_lines)
    print(f"\ndispatched {dispatched}, needs-review {review}, failed {failed}, refused {refused}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
