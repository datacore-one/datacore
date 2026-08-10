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
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from briefing.actions import act  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ops_markers import AUTH_FAILURE_MARKERS  # noqa: E402

CLAIMABLE = "created"
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


def _agent_env() -> dict:
    """Environment for a spawned agent.

    Without a bypass signal, the PreToolUse guard
    (.datacore/lib/hooks/plur_session_guard.py) demands
    `mcp__plur__plur_session_start`. On machines where the PLUR MCP server is
    not connected that guard is UNSATISFIABLE -- the agent cannot call the tool
    that would release it. It refuses every Write and Bash call, writes a lucid
    paragraph explaining why it is stuck, and exits 0. Every task then fails
    its check for a reason that looks nothing like the real one.

    The guard bypasses on any of CLAUDE_AGENT_SDK, OPENCLAW_SESSION,
    NIGHTSHIFT_RUN or DATACORE_HEADLESS. DATACORE_HEADLESS is the right one
    here: this is not nightshift, and claiming its variable would make the
    dispatcher's runs indistinguishable from the overnight executor's to
    anything that keys on it.
    """
    import os
    return {**os.environ, "DATACORE_HEADLESS": "1"}


def run_task(title: str, route: str, cwd: Path) -> tuple[bool, str]:
    """Execute one item. Returns (ok, detail).

    Reads BOTH streams and checks the auth markers: `claude -p` prints auth
    failures to stdout with an empty stderr, and a caller that reads only
    stderr records the failure as the empty string. That exact bug hid a
    nine-day outage on the CoS box -- see ops_markers.
    """
    framing = ROUTE_FRAMING.get(route, ROUTE_FRAMING[DEFAULT_ROUTE])
    prompt = f"{framing}\n\nTask: {title}\n\nBe brief. Report what you found."
    try:
        r = subprocess.run(
            # Without a permission mode the agent is read-only: it declines
            # every write and the check then fails in a way that reads as the
            # agent refusing the task, rather than never having been able to
            # attempt it.
            #
            # NOT --dangerously-skip-permissions, which nightshift's executor
            # uses. That flag is REFUSED outright when the process is root
            # ("cannot be used with root/sudo privileges"), and winston's
            # daemons run as root -- so the box could never execute a single
            # item. `acceptEdits` is accepted as root and is the narrower
            # grant anyway: file edits, not blanket bypass.
            ["claude", "-p", "--permission-mode", "acceptEdits",
             "--output-format", "text", prompt],
            capture_output=True, text=True, timeout=TIMEOUT,
            stdin=subprocess.DEVNULL, cwd=str(cwd), env=_agent_env(),
        )
    except FileNotFoundError:
        return False, "claude CLI not found on PATH"
    except PermissionError as exc:
        return False, f"could not run claude: {exc}"
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TIMEOUT}s"

    combined = f"{r.stdout or ''}\n{r.stderr or ''}".strip()
    marker = next((m for m in AUTH_FAILURE_MARKERS if m in combined.lower()), None)
    if marker:
        return False, f"auth rejected ({marker!r}): {combined[:200]}"
    if r.returncode != 0:
        return False, f"exited {r.returncode}: {combined[:200] or '(no output)'}"
    out = (r.stdout or "").strip()
    if not out:
        return False, "exited 0 but produced no output"

    # An agent that EXPLAINS why it could not do the task exits 0 and produces
    # plenty of text. The first version of this function returned True on any
    # output and recorded two such refusals as DONE -- the same
    # assert-the-exit-code-not-the-outcome bug this system exists to prevent.
    # Text is not evidence of work.
    refusal = next((p for p in REFUSAL_MARKERS if p in out.lower()[:400]), None)
    if refusal:
        return False, f"agent did not complete the task ({refusal!r}): {out[:200]}"
    return True, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True, type=Path)
    ap.add_argument("--actor", default="dispatcher")
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--execute", action="store_true",
                    help="actually claim and run; without it, plan only and write nothing")
    args = ap.parse_args()

    space = args.space.resolve()
    state = fold(read_events(space))
    pending = [i for i in state.items.values() if i.status == CLAIMABLE]
    pending.sort(key=lambda i: i.id)

    if not pending:
        print("nothing to dispatch: no items at status 'created'")
        return 0

    print(f"{len(pending)} claimable item(s); limit {args.limit}")
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
        ok, detail = run_task(title, route, space)
        if ok and check:
            # The ONLY thing that completes an item. An agent that declined
            # produces fluent, confident prose and exits 0; two attempts at
            # sniffing that prose for refusal markers both passed a failure as
            # DONE, because the model rephrases ("I can't" / "I could not").
            # Prose is not evidence. A check that passes is.
            passed = subprocess.run(check, shell=True, cwd=str(space),
                                    capture_output=True, timeout=120).returncode == 0
            if passed:
                act(space, item.id, "complete", args.actor)
                print(f"DONE     [{route}] {title[:70]}\n         -> check passed")
                dispatched += 1
            else:
                EventLog(space, args.actor).append(
                    "item.release", {"id": item.id, "owner": args.actor,
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
