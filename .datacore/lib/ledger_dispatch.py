#!/usr/bin/env python3
"""Claim -> route -> execute -> complete. The consumer the action loop lacked.

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
    ledger_dispatch.py --space DIR [--actor NAME] [--limit N] [--execute]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from briefing.actions import act  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
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



def run_task(title: str, route: str, cwd: Path) -> tuple[bool, str, dict]:
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
    prompt = f"{framing}\n\nTask: {title}\n\nBe brief. Report what you found."

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
    res = ex.run(prompt, timeout_s=TIMEOUT, cwd=cwd)
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
    subprocess.run(["git", "add", "-A"], cwd=space, capture_output=True)
    subprocess.run(["git", "commit", "-m", "dispatch: agent output"],
                   cwd=space, capture_output=True)
    rc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=space,
                        capture_output=True, text=True)
    if rc.returncode != 0:
        print("         -> check FAILED CLOSED: cannot resolve HEAD for isolation")
        return False, ""
    head = rc.stdout.strip()
    with tempfile.TemporaryDirectory(prefix="check-") as tmp:
        wt = Path(tmp) / "verify"
        add = subprocess.run(["git", "worktree", "add", "--detach", str(wt), head],
                             cwd=space, capture_output=True, text=True)
        if add.returncode != 0:
            print(f"         -> check FAILED CLOSED: no isolated worktree "
                  f"({(add.stderr or '').strip()[:90]})")
            return False, head
        try:
            ok = subprocess.run(check, shell=True, cwd=str(wt),
                                capture_output=True, timeout=120).returncode == 0
            return ok, head
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                           cwd=space, capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True, type=Path)
    ap.add_argument("--actor", default="dispatcher")
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
    # racy across an eventually-consistent log. Items with no assignee stay
    # open to whoever gets there first -- the existing behaviour, kept so
    # nothing already in flight changes meaning.
    addressed = [i for i in pending
                 if (i.payload or {}).get("assignee") not in (None, "", args.actor)]
    pending = [i for i in pending if i not in addressed]

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
            iid = (ev.payload or {}).get("id")
            if iid:
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
            {"id": item.id, "owner": args.actor,
             "reason": f"gave up after {n} failed attempts"})
        print(f"DEADLETTER  {title}\n         -> {n} failed attempts; dismissed")
    pending = [i for i in pending if attempts.get(i.id, 0) < MAX_ATTEMPTS]
    pending.sort(key=lambda i: i.id)

    mirror_note = f" ({mirrored} org-mirrored task(s) skipped -- not delegations)" if mirrored else ""
    if addressed:
        mirror_note += f"; {len(addressed)} addressed to another agent"
    if not pending:
        print(f"nothing to dispatch: no delegated items awaiting claim{mirror_note}")
        return 0

    print(f"{len(pending)} delegated item(s) awaiting claim; limit {args.limit}{mirror_note}")
    dispatched = failed = refused = review = 0

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

        # Claim BEFORE working, so an interrupted run is visible as claimed.
        EventLog(space, args.actor).append(
            "item.claim", {"id": item.id, "owner": args.actor, "route": route, "reason": why})
        ok, detail, meta = run_task(title, route, space)
        if ok and check:
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
                print(f"DONE     [{route}] {title[:70]}\n         -> check passed "
                      f"@ {sha[:10]} ({meta.get('cost_cents', '?')}c, "
                      f"{meta.get('duration_s', '?')}s)")
                dispatched += 1
            else:
                EventLog(space, args.actor).append(
                    "item.release", {"id": item.id, "owner": args.actor,
                                     "artifact_commit": sha,
                                     "error": f"check failed: {check}"})
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
            EventLog(space, args.actor).append(
                "item.release", {"id": item.id, "owner": args.actor, "error": detail[:300]})
            print(f"FAILED   [{route}] {title[:70]}\n         -> {detail[:150]}")
            failed += 1

    print(f"\ndispatched {dispatched}, needs-review {review}, failed {failed}, refused {refused}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
