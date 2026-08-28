#!/usr/bin/env python3
"""
session_learning_sweep.py — extract learning from a whole day of sessions, once.

UPSTREAM CANDIDATE: PLUR CORE (decided 2026-08-17), alongside
`session_archive.py`. "Sweep yesterday's sessions and write the engrams" is the
memory engine's own job, not a Datacore workflow.

NOT plur-encode, and the distinction is load-bearing. plur-encode is the
enterprise substrate for extracting engrams from many data sources, growing per
client — staged datasets, resume, content-hashed prompts, two model profiles,
and a receipt chain from every claim back to the merge request or revert that
produced it. That machinery is worth its cost because its evidence unit is a
decision WITH AN OUTCOME. A conversation transcript mostly is not: I said
something, nobody objected, we moved on. The correction turns are real evidence;
the other 95% is absence of contradiction. So this stays a cron job and one
`claude -p`, and if it ever grows a second stage that is the signal to reach for
encode rather than to reimplement it here.

REPLACES /wrap-up §5 and §6. Those spawned a session-learning-coordinator and a
learning-classifier inside every interactive close: median 405k output tokens
and ~14 subagent transcripts per wrap-up, for 3.6 engrams. Worse, the classifier
never terminated — 5-plur's cursor sat at 2026-07-30 while four consecutive
passes re-read the same file and appended another run note explaining why they
weren't advancing it, and all 37 engram candidates it queued are still
`status: candidate` because /today never had a step to drain them.

THE WORK QUEUE IS PER-SESSION, NOT A DATE CURSOR. `learning_status` in each
archived session's meta.json is claimed exactly once. That is the structural
fix for the stalled cursor: there is no "advance past 2026-07-30" operation to
get wrong, and a session can never be skipped by advancing a date over it.

NO CANDIDATE QUEUE. The sweep writes engrams via plur_learn or it writes
nothing. A deferral path that nobody drains is not caution, it is a leak — see
the 37.

THE SCHEDULE MUST MATCH THE QUEUE. The queue is per-session, but the job used
to sweep `--date yesterday` and nothing else, so any session a night left
behind — batch cap, failure, laptop asleep — was stranded permanently, because
no later run would ever look at an older day again. `--backlog` is what the
nightly job runs: every day still holding pending sessions, oldest first.

Usage:
  python3 .datacore/lib/session_learning_sweep.py --backlog        # every pending day
  python3 .datacore/lib/session_learning_sweep.py                  # yesterday
  python3 .datacore/lib/session_learning_sweep.py --date 2026-08-16
  python3 .datacore/lib/session_learning_sweep.py --backlog --dry-run
  python3 .datacore/lib/session_learning_sweep.py --status         # queue depth
  python3 .datacore/lib/session_learning_sweep.py --drain-candidates
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
ARCHIVE_DIR = DATACORE_ROOT / ".datacore" / "state" / "sessions" / "archive"
LOG = Path.home() / ".datacore" / "state" / "session-learning-sweep.log"

# A session below both thresholds produced nothing worth a pattern. Sweeping it
# costs a full agent turn to conclude "nothing here", which over a month is most
# of the budget. Marked `skipped`, not `done`, so the distinction stays visible.
MIN_TURNS = 12
MIN_OUTPUT_TOKENS = 8_000

# The sweep's own `claude -p` runs are archived like any other session and land
# back in this queue, so without this the sweep sweeps itself: by 2026-08-20 the
# recursion had reached fifth order (6dd6a2d3 -> 11d46a2f -> a8c372a5 ->
# 433fb202 -> ...), each night spending its whole budget re-mining the previous
# night's transcript while real sessions waited (ENG-2026-08-20-024/-035). They
# clear both thresholds easily (45-85 turns, 21k-48k tokens), so the cheap guard
# never catches them. Matched on the first line the prompt template below emits.
# Marked `skipped`, not `done`, so a self-run stays visible in the archive.
SELF_SWEEP_PREFIX = "Extract durable learning from"

# One prompt cannot carry a whole day of raw transcript. The agent is given the
# precomputed summaries and the PATHS, and reads what it judges relevant with
# its own tools — the same reason /today's orchestrator hands over paths.
MAX_SESSIONS_PER_BATCH = 12

# A batch is bounded by TOKENS as well as by count, because count alone cannot
# express "too big to finish". 2026-08-18: the twelve largest sessions of
# 2026-08-17 summed to ~1.24M output tokens, `claude -p` hit the 1800s wall
# every time, and the failure path correctly re-marked all twelve `pending` —
# so the identical oversized batch was retried forever. Sorting biggest-first
# guaranteed the worst possible batch.
BATCH_TOKEN_BUDGET = 250_000
CLAUDE_TIMEOUT = 1800

# Escalation ladder for a batch that keeps failing: retry in a batch, then
# isolate the session so one poison transcript cannot hold its whole day
# hostage, then give up VISIBLY as `failed`. Pending-forever is not a terminal
# state, it is a leak that looks like a queue.
ISOLATE_AFTER = 2
FAIL_AFTER = 4

# The nightly job starts at 01:00; it must be done long before /today at 08:00.
# Sessions left over stay pending and are picked up by the next night's backlog
# pass — which is the point of having one.
RUN_DEADLINE_SECONDS = 4 * 3600


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def pending_sessions(day: str) -> tuple[list[dict], list[dict]]:
    """Return (to_sweep, to_skip) for a day, both from `pending` metas only."""
    d = ARCHIVE_DIR / day
    if not d.is_dir():
        return [], []
    sweep, skip = [], []
    for meta_path in sorted(d.glob("*/meta.json")):
        m = _load(meta_path)
        if not m or m.get("learning_status") != "pending":
            continue
        m["_meta_path"] = str(meta_path)
        if (m.get("first_prompt") or "").startswith(SELF_SWEEP_PREFIX):
            m["_skip_reason"] = "self-sweep: extraction run, not a work session"
            skip.append(m)
        elif m.get("turns", 0) < MIN_TURNS and m.get("output_tokens", 0) < MIN_OUTPUT_TOKENS:
            m["_skip_reason"] = "below MIN_TURNS/MIN_OUTPUT_TOKENS"
            skip.append(m)
        else:
            sweep.append(m)
    # Biggest first: if the batch cap bites, it should bite the trivia.
    sweep.sort(key=lambda m: m.get("output_tokens", 0), reverse=True)
    return sweep, skip


def days_with_pending() -> list[str]:
    """Every archived day still holding pending sessions, oldest first.

    Directory names are YYYY-MM-DD, so lexicographic sort is chronological.
    """
    if not ARCHIVE_DIR.is_dir():
        return []
    days = []
    for d in sorted(ARCHIVE_DIR.iterdir()):
        if not d.is_dir():
            continue
        if any(_load(mp).get("learning_status") == "pending"
               for mp in d.glob("*/meta.json")):
            days.append(d.name)
    return days


def make_batch(sweep: list[dict]) -> list[dict]:
    """Pick the next batch, bounded by count AND by total output tokens.

    A session that has already failed ISOLATE_AFTER times travels alone. If one
    transcript is what blows the timeout, batching it again just re-fails every
    session it is grouped with — which is precisely how 2026-08-17 got stuck.
    """
    stuck = [m for m in sweep if m.get("learning_attempts", 0) >= ISOLATE_AFTER]
    if stuck:
        return [max(stuck, key=lambda m: m.get("learning_attempts", 0))]

    batch: list[dict] = []
    total = 0
    for m in sweep:
        tok = m.get("output_tokens", 0)
        # The first session is always admitted: a single session larger than the
        # whole budget still has to be attempted, alone, or it never runs.
        if batch and (total + tok > BATCH_TOKEN_BUDGET
                      or len(batch) >= MAX_SESSIONS_PER_BATCH):
            break
        batch.append(m)
        total += tok
    return batch


def mark(meta_path: str, status: str, result: str | None = None,
         bump_attempt: bool = False) -> None:
    p = Path(meta_path)
    m = _load(p)
    if not m:
        return
    m["learning_status"] = status
    m["learning_run_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    if bump_attempt:
        m["learning_attempts"] = m.get("learning_attempts", 0) + 1
    if result is not None:
        m["learning_result"] = result[:2000]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=2))
    tmp.replace(p)


def build_prompt(day: str, sessions: list[dict]) -> str:
    lines = [
        f"Extract durable learning from {len(sessions)} Claude Code session(s) "
        f"recorded on {day}. You are running unattended from cron.",
        "",
        "## Sessions",
        "",
    ]
    for i, m in enumerate(sessions, 1):
        d = Path(m["_meta_path"]).parent
        lines += [
            f"### {i}. {m['session_id'][:8]}  ({m.get('turns', 0)} turns, "
            f"{m.get('output_tokens', 0):,} output tokens)",
            f"- transcript: `{d}/transcript.jsonl.gz`  (gzipped JSONL, one record per line)",
            f"- subagents:  {m.get('subagent_transcripts', 0)} under `{d}/subagents/`",
            f"- spaces touched: {', '.join(m.get('spaces_touched') or ['(none)'])}",
            f"- files modified: {', '.join((m.get('files_modified') or [])[:12]) or '(none)'}",
            f"- opened with: {(m.get('first_prompt') or '')[:300]!r}",
            "",
        ]
    lines += [
        "## What to do",
        "",
        "1. Read each transcript (`gzip -dc` or python gzip). Skim for the things below;",
        "   you do NOT need to read every line of a large one.",
        "2. Extract ONLY:",
        "   - **corrections** — the user said you were wrong, and why",
        "   - **patterns** — a reusable technique or discovery that will apply again",
        "   - **preferences** — a stated 'always X' / 'never Y'",
        "   Skip anything derivable from the code, anything true only for that",
        "   session, and anything already obvious from the repo.",
        "3. For EACH item, call `plur_similarity_search` first. If something above",
        "   0.9 already exists, call `plur_feedback` positively on it and move on.",
        "   Otherwise call `plur_learn` and WRITE IT NOW.",
        "",
        "   **Do not queue candidates. Do not write an engrams-candidates-*.yaml.**",
        "   Deferring is what left 37 candidates unreviewed since 2026-07-30. If an",
        "   item is not worth writing, drop it — that is a real answer.",
        "",
        "4. Set `scope` per engram by content, not per run: team/engineering facts to",
        "   the matching group scope, personal workflow to the default. Root-level",
        "   system work is scope `global` unless it is space-specific.",
        "5. Append the same items to the learning files of the space each belongs to:",
        "   `<space>/.datacore/learning/{patterns,corrections,preferences}.md`, under a",
        "   `## YYYY-MM-DD: Title` heading, citing the engram ID you just created.",
        "   Sessions whose `spaces touched` is `root` write to",
        "   `.datacore/learning/` at the installation root.",
        "",
        "## Report",
        "",
        "Finish with a single line of JSON and nothing after it:",
        '{"engrams_created": N, "recurrences_reinforced": N, "sessions_read": N, '
        '"notes": "one sentence"}',
        "",
        "Be terse. Do not summarise the sessions back to me — the transcripts are",
        "already on disk. Cross-session repetition is the signal worth reporting: if",
        "the same correction shows up in three sessions, that is one engram with a",
        "strong rationale, not three.",
    ]
    return "\n".join(lines)


def run_claude(prompt: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions",
             "--output-format", "text", prompt],
            cwd=str(DATACORE_ROOT), capture_output=True, text=True,
            timeout=CLAUDE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"claude -p timed out after {CLAUDE_TIMEOUT}s"
    except FileNotFoundError:
        return False, "claude CLI not found on PATH"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:300]}"

    out = (r.stdout or "").strip()
    if r.returncode != 0 and not out:
        return False, f"claude -p exited {r.returncode}: {(r.stderr or '')[:300] or '(no output)'}"
    if not out:
        # An empty success is the auth-outage signature that hid a nine-day
        # failure in ledger_transport — never report it as done.
        return False, "claude -p succeeded but returned nothing"
    return True, out


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {msg}\n")


def cmd_status() -> int:
    if not ARCHIVE_DIR.is_dir():
        print("no archive yet")
        return 0
    total = 0
    for day_dir in sorted(ARCHIVE_DIR.iterdir()):
        if not day_dir.is_dir():
            continue
        counts: dict[str, int] = {}
        for mp in day_dir.glob("*/meta.json"):
            s = _load(mp).get("learning_status", "?")
            counts[s] = counts.get(s, 0) + 1
        total += counts.get("pending", 0)
        print(f"{day_dir.name}  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"\n{total} session(s) pending across all days")
    return 0


def cmd_drain_candidates() -> int:
    """Report the legacy candidate queue. Deliberately does NOT auto-import.

    37 candidates accumulated behind a review step that never existed. Writing
    them all now would promote 37 unreviewed claims to memory in one shot —
    exactly the outcome the review gate was meant to prevent. Surface them and
    let a human or a scoped session decide.
    """
    files = sorted(DATACORE_ROOT.glob("**/engrams-candidates-*.yaml"))
    files = [f for f in files if "node_modules" not in f.parts]
    total = 0
    for f in files:
        n = f.read_text(errors="replace").count("status: candidate")
        total += n
        print(f"{n:4d}  {f.relative_to(DATACORE_ROOT)}")
    print(f"\n{total} unreviewed candidate(s) in {len(files)} file(s).")
    print("These predate the per-session queue and are NOT swept automatically.")
    print("Review with /daily-review, or delete the files to drop them.")
    return 0


def sweep_once(day: str, dry_run: bool = False) -> str:
    """Run ONE batch for a day. Returns done | empty | failed | dry-run."""
    sweep, skip = pending_sessions(day)

    # --dry-run means invoke nothing, and marking a meta is a mutation: it
    # claims the session out of the queue for good. Previously a dry run
    # silently marked every trivial session `skipped` before printing.
    if not dry_run:
        for m in skip:
            mark(m["_meta_path"], "skipped",
                 m.get("_skip_reason", "below MIN_TURNS/MIN_OUTPUT_TOKENS"))

    if not sweep:
        msg = f"{day}: 0 session(s) to sweep ({len(skip)} skipped)"
        print(msg)
        log(msg)
        return "empty"

    batch = make_batch(sweep)
    remaining = len(sweep) - len(batch)
    tokens = sum(m.get("output_tokens", 0) for m in batch)
    prompt = build_prompt(day, batch)

    if dry_run:
        print(prompt)
        print(f"\n--- would sweep {len(batch)} session(s) / {tokens:,} output tokens, "
              f"skip {len(skip)}, leaving {remaining} pending for the next batch ---")
        return "dry-run"

    log(f"{day}: sweeping {len(batch)} session(s), {tokens:,} output tokens")
    ok, out = run_claude(prompt)

    if not ok:
        # Never mark failed work done — the next run would skip it forever.
        # But pending-forever is its own leak, so count attempts and eventually
        # settle on `failed`, which is visible in --status.
        for m in batch:
            attempts = m.get("learning_attempts", 0) + 1
            if attempts >= FAIL_AFTER and len(batch) == 1:
                mark(m["_meta_path"], "failed",
                     f"gave up after {attempts} attempts: {out}", bump_attempt=True)
            else:
                mark(m["_meta_path"], "pending", f"FAILED: {out}", bump_attempt=True)
        print(f"{day}: sweep FAILED on {len(batch)} session(s) / {tokens:,} tokens — {out}")
        log(f"{day}: FAILED ({len(batch)} sessions, {tokens:,} tokens) {out}")
        return "failed"

    for m in batch:
        mark(m["_meta_path"], "done", out[-2000:])

    tail = out.strip().splitlines()[-1] if out.strip() else ""
    print(f"{day}: swept {len(batch)} session(s) / {tokens:,} tokens, "
          f"skipped {len(skip)}"
          + (f", {remaining} still pending" if remaining else ""))
    print(f"  agent report: {tail[:300]}")
    log(f"{day}: OK {len(batch)} swept | {tail[:200]}")
    return "done"


def run_day(day: str, deadline: float | None, dry_run: bool = False) -> str:
    """Sweep a day in batches until it drains, fails, or the deadline hits."""
    while True:
        outcome = sweep_once(day, dry_run)
        if outcome != "done":
            return outcome
        if deadline is not None and time.monotonic() >= deadline:
            msg = f"{day}: stopped on the run deadline — remaining sessions stay pending"
            print(msg)
            log(msg)
            return "deadline"


def cmd_backlog(dry_run: bool) -> int:
    """Sweep EVERY day with pending sessions, oldest first.

    The scheduled job runs this rather than a bare `--date yesterday`. A
    yesterday-only sweep can never revisit an older day, so anything a night
    left behind — a failure, a batch cap, a laptop asleep — was stranded
    permanently. The queue is per-session; the schedule has to match it.
    """
    days = days_with_pending()
    if not days:
        print("backlog: nothing pending")
        log("backlog: nothing pending")
        return 0

    print(f"backlog: {len(days)} day(s) with pending sessions — {', '.join(days)}")
    log(f"backlog: {len(days)} day(s) pending — {', '.join(days)}")

    deadline = None if dry_run else time.monotonic() + RUN_DEADLINE_SECONDS
    unfinished = []
    for i, day in enumerate(days):
        if deadline is not None and time.monotonic() >= deadline:
            left = days[i:]
            print(f"backlog: deadline reached, {len(left)} day(s) untouched: {', '.join(left)}")
            log(f"backlog: deadline reached, untouched: {', '.join(left)}")
            unfinished += left
            break
        # One bad day must not block the rest of the backlog.
        if run_day(day, deadline, dry_run) in ("failed", "deadline"):
            unfinished.append(day)

    if unfinished:
        print(f"backlog: {len(unfinished)} day(s) did not drain: {', '.join(unfinished)}")
        log(f"backlog: did not drain {', '.join(unfinished)}")
        return 1
    print("backlog: drained")
    log("backlog: drained")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--backlog", action="store_true",
                    help="sweep every day with pending sessions, oldest first")
    ap.add_argument("--dry-run", action="store_true", help="print the prompt, invoke nothing")
    ap.add_argument("--status", action="store_true", help="show queue depth per day")
    ap.add_argument("--drain-candidates", action="store_true",
                    help="report the legacy engrams-candidates-*.yaml backlog")
    args = ap.parse_args()

    if args.status:
        return cmd_status()
    if args.drain_candidates:
        return cmd_drain_candidates()
    if args.backlog:
        return cmd_backlog(args.dry_run)

    day = args.date or (date.today() - timedelta(days=1)).isoformat()
    deadline = None if args.dry_run else time.monotonic() + RUN_DEADLINE_SECONDS
    return 1 if run_day(day, deadline, args.dry_run) == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
