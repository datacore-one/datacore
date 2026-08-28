#!/usr/bin/env python3
"""
session_archive.py — copy a finished Claude Code session into Datacore.

UPSTREAM CANDIDATE: PLUR CORE (decided 2026-08-17). Nothing here is
Datacore-specific except the archive path — "keep the transcript before the
harness prunes it, and record what it contained" is a memory-engine concern,
and every PLUR user has the same one-month cliff. The companion sweep
(`session_learning_sweep.py`) belongs with it. Deliberately NOT plur-encode:
encode is the enterprise substrate for multi-source, client-specific extraction
with receipts; this is a cron job and one `claude -p`, and it should stay that
simple. Keep this file dependency-free of Datacore internals so the port stays
a move rather than a rewrite.

WHY THIS EXISTS. `~/.claude/projects/` is pruned. On 2026-08-16 an audit of
/wrap-up cost could only reach back to 2026-07-16 — one month — because every
older transcript was already gone. The session is the raw material for every
learning pass, journal entry and meta-analysis this system does, and it was
living somewhere with a retention policy nobody chose.

It also decouples learning from wrap-up. Once the day's sessions are all on
disk in one predictable place, pattern extraction stops being 14 subagents
spawned inside an interactive close (median 405k output tokens, ~3.6 engrams)
and becomes one batch job that reads N sessions at 05:00. `learning_status`
in meta.json is that job's work queue.

LAYOUT

    .datacore/state/sessions/               <- live per-session state (existing)
      <session_id>.json
      archive/
        <YYYY-MM-DD>/
          <session_id>/
            transcript.jsonl.gz
            subagents/<uuid>.jsonl.gz
            meta.json

The archive lives under a subdirectory because `cleanup_all_sessions()` globs
`*.json` at the top level and would otherwise be one refactor away from
deleting the corpus it exists to preserve.

IDEMPOTENT. Safe to call from the SessionEnd hook, /wrap-up and /continue in
the same session — a re-run refreshes the copy and recomputes meta, but never
resets `learning_status` once the sweep has claimed it.

Usage:
  python3 .datacore/lib/session_archive.py                    # current session
  python3 .datacore/lib/session_archive.py --session <uuid>
  python3 .datacore/lib/session_archive.py --backfill 30      # last N days
  python3 .datacore/lib/session_archive.py --list 2026-08-16  # what's archived
  python3 .datacore/lib/session_archive.py --json             # machine-readable
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
PROJECTS_DIR = Path.home() / ".claude" / "projects"
ARCHIVE_DIR = DATACORE_ROOT / ".datacore" / "state" / "sessions" / "archive"
STATE_DIR = DATACORE_ROOT / ".datacore" / "state" / "sessions"

# Markers that say a session ran /wrap-up. Recorded in meta so the sweep can
# tell a closed session from one that was abandoned mid-flight.
#
# ONLY the command-invocation form. A bare "/wrap-up" also appears whenever the
# command is merely discussed — this file's own authoring session matched it
# three times and reported wrapped=true while never once closing.
WRAPUP_MARKERS = ("<command-name>/wrap-up", "<command-name>wrap-up")


# --------------------------------------------------------------------------
# locating the transcript
# --------------------------------------------------------------------------

def find_transcript(session_id: str) -> Path | None:
    """Find a session's transcript across every project directory.

    Deriving the project slug from cwd would be faster, but wrong the moment a
    session starts in a subdirectory (`.datacore/dips` becomes
    `-Users-gregor-Data--datacore-dips`, with dots collapsed to dashes too).
    A glob is cheap and cannot be wrong.
    """
    if not PROJECTS_DIR.is_dir():
        return None
    direct = list(PROJECTS_DIR.glob(f"*/{session_id}.jsonl"))
    return direct[0] if direct else None


def find_subagents(transcript: Path) -> list[Path]:
    """Subagent transcripts live in <session_id>/subagents/ beside the parent."""
    d = transcript.parent / transcript.stem / "subagents"
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


# --------------------------------------------------------------------------
# reading a transcript
# --------------------------------------------------------------------------

def _text(msg) -> str:
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict):
        c = msg.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return " ".join(
                b.get("text", "") for b in c
                if isinstance(b, dict) and b.get("type") == "text"
            )
    return ""


def _iter_records(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def summarize(transcript: Path, subagents: list[Path]) -> dict:
    """Everything the sweep and the meta-analysis need, computed once here.

    Doing this at archive time rather than at read time means the cron job and
    /wrap-up §9 both read numbers instead of re-parsing a multi-megabyte file,
    and the two can never disagree about what the session contained.
    """
    tokens = Counter()
    tools = Counter()
    agents = Counter()
    files: set[str] = set()
    turns = 0
    user_turns = 0
    first_prompt = ""
    wrapped = False
    stamps: list[str] = []

    for rec in _iter_records(transcript):
        ts = rec.get("timestamp")
        if ts:
            stamps.append(ts)
        rtype = rec.get("type")

        if rtype == "user":
            body = _text(rec.get("message", {}))
            # Hook output and tool results also arrive as user records; only
            # count something that looks like a person typing.
            if body and not body.startswith("<"):
                user_turns += 1
                if not first_prompt:
                    first_prompt = body[:500]
            if any(m in body for m in WRAPUP_MARKERS):
                wrapped = True

        elif rtype == "assistant":
            turns += 1
            u = (rec.get("message") or {}).get("usage") or {}
            for k in ("input_tokens", "cache_creation_input_tokens",
                      "cache_read_input_tokens", "output_tokens"):
                tokens[k] += u.get(k, 0)
            for blk in (rec.get("message") or {}).get("content") or []:
                if not (isinstance(blk, dict) and blk.get("type") == "tool_use"):
                    continue
                name = blk.get("name")
                tools[name] += 1
                inp = blk.get("input") or {}
                if name in ("Task", "Agent"):
                    agents[inp.get("subagent_type") or "unspecified"] += 1
                elif name in ("Edit", "Write", "NotebookEdit"):
                    fp = inp.get("file_path")
                    if fp:
                        files.add(fp)

    sub_tokens = Counter()
    sub_turns = 0
    for sp in subagents:
        for rec in _iter_records(sp):
            if rec.get("type") != "assistant":
                continue
            sub_turns += 1
            u = (rec.get("message") or {}).get("usage") or {}
            for k in ("input_tokens", "cache_creation_input_tokens",
                      "cache_read_input_tokens", "output_tokens"):
                sub_tokens[k] += u.get(k, 0)
            # Subagent writes count as this session's work. Omitting them
            # under-reported a 2026-08-01 session by 4 files — including two
            # next_actions.org and a learning file, all written by spawned
            # agents. Session-scoped push reads this set, so a gap here means
            # real work silently never leaves the machine.
            for blk in (rec.get("message") or {}).get("content") or []:
                if (isinstance(blk, dict) and blk.get("type") == "tool_use"
                        and blk.get("name") in ("Edit", "Write", "NotebookEdit")):
                    fp = (blk.get("input") or {}).get("file_path")
                    if fp:
                        files.add(fp)

    # Which spaces the session touched, from the paths it wrote. Used by the
    # sweep to route patterns to the right space without asking an LLM.
    #
    # Work on root system files (.datacore/lib, .datacore/commands) reports as
    # "root", NOT as nothing. Treating it as nothing is the documented failure
    # of session-learning-coordinator — a session that only touched the system
    # itself got silently skipped, which is most Datacore-development sessions.
    spaces: set[str] = set()
    for f in files:
        try:
            parts = Path(f).relative_to(DATACORE_ROOT).parts
        except ValueError:
            continue  # outside the installation (worktree, /tmp) — not a space
        if not parts:
            continue
        spaces.add(parts[0] if parts[0][:1].isdigit() else "root")
    spaces_touched = sorted(spaces)

    return {
        "first_prompt": first_prompt,
        "wrapped": wrapped,
        "started_at": min(stamps) if stamps else None,
        "ended_at": max(stamps) if stamps else None,
        "turns": turns,
        "user_turns": user_turns,
        "tokens": dict(tokens),
        "output_tokens": tokens["output_tokens"],
        "subagent_transcripts": len(subagents),
        "subagent_turns": sub_turns,
        "subagent_tokens": dict(sub_tokens),
        "tool_calls": dict(tools.most_common()),
        "tool_call_total": sum(tools.values()),
        "agents_spawned": dict(agents),
        "files_modified": sorted(files),
        "spaces_touched": spaces_touched,
    }


# --------------------------------------------------------------------------
# archiving
# --------------------------------------------------------------------------

def _gzip_copy(src: Path, dst: Path) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as f_in, gzip.open(dst, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out, length=1024 * 1024)
    return dst.stat().st_size


def _session_date(summary: dict, transcript: Path) -> str:
    """The day the session BELONGS to, not the day it was archived.

    A session that runs past midnight archives under the day it started —
    otherwise the sweep's 'all of today's sessions' would silently split one
    evening's work across two batches.
    """
    started = summary.get("started_at")
    if started:
        try:
            return datetime.fromisoformat(
                started.replace("Z", "+00:00")
            ).astimezone().date().isoformat()
        except ValueError:
            pass
    return datetime.fromtimestamp(transcript.stat().st_mtime).date().isoformat()


def archive(session_id: str, force: bool = False, initial_status: str = "pending") -> dict:
    """Copy one session into the archive. Idempotent.

    `initial_status` is what a NEW meta gets. Live archiving uses "pending" so
    the nightly sweep picks it up; --backfill uses "backfilled", because
    retroactively queueing 302 historical sessions would present a permanent
    300-deep backlog that nobody intends to run and that makes the real queue
    depth unreadable.
    """
    transcript = find_transcript(session_id)
    if not transcript:
        return {"session_id": session_id, "status": "no-transcript"}

    subagents = find_subagents(transcript)
    summary = summarize(transcript, subagents)
    day = _session_date(summary, transcript)
    dest = ARCHIVE_DIR / day / session_id
    meta_path = dest / "meta.json"

    # Preserve the sweep's cursor across re-archives. A session re-archived by
    # /wrap-up after the cron already processed it must NOT go back in the
    # queue, or the sweep re-learns the same session every night forever.
    prior = {}
    if meta_path.exists():
        try:
            prior = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            prior = {}
    if force:
        learning_status = initial_status          # explicit re-queue
    else:
        learning_status = prior.get("learning_status") or initial_status

    dest.mkdir(parents=True, exist_ok=True)
    size = _gzip_copy(transcript, dest / "transcript.jsonl.gz")
    for sp in subagents:
        size += _gzip_copy(sp, dest / "subagents" / f"{sp.stem}.jsonl.gz")

    # Anything the live state file knows that the transcript does not.
    state_file = STATE_DIR / f"{session_id}.json"
    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except (OSError, ValueError):
            state = {}

    meta = {
        "session_id": session_id,
        "date": day,
        "archived_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_transcript": str(transcript),
        "archive_bytes": size,
        "learning_status": learning_status,
        "learning_run_at": prior.get("learning_run_at"),
        "learning_result": prior.get("learning_result"),
        "cwd": state.get("cwd") or os.getcwd(),
        **summary,
    }
    if state.get("first_prompt") and not meta.get("first_prompt"):
        meta["first_prompt"] = state["first_prompt"]

    tmp = meta_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, indent=2))
    tmp.replace(meta_path)

    return {
        "session_id": session_id,
        "status": "archived",
        "date": day,
        "path": str(dest),
        "bytes": size,
        "turns": summary["turns"],
        "output_tokens": summary["output_tokens"],
        "subagents": summary["subagent_transcripts"],
        "learning_status": learning_status,
    }


def backfill(days: int, status: str = "backfilled") -> list[dict]:
    """Archive every transcript touched in the last N days.

    Two callers, two intents. The one-shot historical rescue wants
    status="backfilled" (archive it, never queue it). The nightly cron wants
    status="pending" over a 2-day window, to catch sessions whose SessionEnd
    hook was killed, timed out, or never fired because the terminal was closed
    outright — belt and braces for the archive that everything downstream reads.
    """
    cutoff = time.time() - days * 86400
    out = []
    seen = set()
    for p in PROJECTS_DIR.glob("*/*.jsonl"):
        if p.stat().st_mtime < cutoff or p.stem in seen:
            continue
        seen.add(p.stem)
        try:
            out.append(archive(p.stem, initial_status=status))
        except Exception as e:  # one bad transcript must not stop the sweep
            out.append({"session_id": p.stem, "status": "error", "error": str(e)})
    return out


def requeue(day: str) -> int:
    """Move a day's `backfilled` sessions into the sweep queue.

    The escape hatch for "actually, do learn from that week" — explicit, per
    day, never implicit.
    """
    n = 0
    for meta_path in (ARCHIVE_DIR / day).glob("*/meta.json"):
        try:
            m = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            continue
        if m.get("learning_status") != "backfilled":
            continue
        m["learning_status"] = "pending"
        meta_path.write_text(json.dumps(m, indent=2))
        n += 1
    return n


def list_day(day: str) -> list[dict]:
    d = ARCHIVE_DIR / day
    if not d.is_dir():
        return []
    out = []
    for meta_path in sorted(d.glob("*/meta.json")):
        try:
            out.append(json.loads(meta_path.read_text()))
        except (OSError, ValueError):
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--session", help="session id (default: CLAUDE_CODE_SESSION_ID)")
    ap.add_argument("--backfill", type=int, metavar="DAYS",
                    help="archive every transcript modified in the last N days")
    ap.add_argument("--status", choices=["pending", "backfilled"], default="backfilled",
                    help="learning_status for NEW metas during --backfill "
                         "(default: backfilled = archive but never queue)")
    ap.add_argument("--list", metavar="YYYY-MM-DD", help="list archived sessions for a day")
    ap.add_argument("--force", action="store_true",
                    help="re-queue for learning even if already done")
    ap.add_argument("--requeue", metavar="YYYY-MM-DD",
                    help="move that day's backfilled sessions into the sweep queue")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.requeue:
        n = requeue(args.requeue)
        print(json.dumps({"requeued": n, "date": args.requeue}) if args.json
              else f"requeued {n} session(s) for {args.requeue}")
        return 0

    if args.list:
        rows = list_day(args.list)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            for r in rows:
                print(f"{r['session_id'][:8]}  {r.get('learning_status','?'):8} "
                      f"turns={r.get('turns',0):4}  out={r.get('output_tokens',0):>9,}  "
                      f"{(r.get('first_prompt') or '')[:60]!r}")
            print(f"\n{len(rows)} session(s) archived for {args.list}")
        return 0

    if args.backfill:
        rows = backfill(args.backfill, status=args.status)
        ok = [r for r in rows if r["status"] == "archived"]
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            total = sum(r.get("bytes", 0) for r in ok)
            print(f"archived {len(ok)}/{len(rows)} session(s), {total / 1e6:.1f} MB compressed")
            for r in rows:
                if r["status"] != "archived":
                    print(f"  {r['status']}: {r['session_id']}")
        return 0

    sid = args.session or os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if not sid:
        # Silent no-op, not an error: the SessionEnd hook fires in contexts
        # that have no session id, and a hook that fails there is a hook that
        # gets disabled.
        print(json.dumps({"status": "no-session-id"}) if args.json else
              "no session id available (set --session or CLAUDE_CODE_SESSION_ID)")
        return 0

    result = archive(sid, force=args.force)
    if args.json:
        print(json.dumps(result, indent=2))
    elif result["status"] == "archived":
        print(f"archived {result['session_id'][:8]} -> {result['path']} "
              f"({result['bytes'] / 1e6:.1f} MB, {result['turns']} turns, "
              f"learning={result['learning_status']})")
    else:
        print(f"{result['status']}: {result['session_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
