#!/usr/bin/env python3
"""extract_recall_events.py — parse Claude Code session transcripts and
emit one structured record per plur_recall* / plur_inject* / plur_session_start
tool call, with the engram IDs that came back.

This is the LOCAL counterpart to the server-side `api_engram_recall` audit
events added in plur-ai/enterprise PR #100. Local PLUR (the ~/.plur store)
doesn't currently log recalls server-side — this script post-hoc parses
the transcript backup to produce the same shape of data.

OUTPUT
  One JSONL file per source date, written to:
    ~/.plur/observations/recall-YYYY-MM-DD.jsonl
  Each record:
    {
      "ts":              "2026-05-17T12:34:56.789Z",
      "session":         "b1c30f97-fc29-475a-b6ac-20c2242da634",
      "session_project": "-Users-gregor-Data",
      "source":          "tool" | "hook",
      "tool":            "mcp__plur__plur_recall_hybrid" | "hook_attachment",
      "query":           "first 200 chars of args.query or args.task",
      "engram_ids":      ["ENG-2026-...","ABS-..."],
      "engram_count":    7,
      "turn_index":      4287,
      "cwd":             "&lt;cwd from session metadata&gt;"
    }

  source="tool" → engram IDs came back as the result of an explicit
  plur_recall* MCP call.
  source="hook" → engram IDs were injected via PLUR's UserPromptSubmit /
  command-recall-inject hooks (appears as "attachment" type messages in
  the Claude Code transcript, not as tool_use). These were invisible to
  the previous version of this extractor — about 3× the volume of tool
  recalls on a heavy session.

USAGE
  # One-shot: parse everything in the trace backup
  python3 extract_recall_events.py

  # Specific date range
  python3 extract_recall_events.py --since 2026-05-15 --until 2026-05-17

  # One session by uuid
  python3 extract_recall_events.py --session b1c30f97-fc29-475a-b6ac-20c2242da634

  # Dry-run — print counts, don't write
  python3 extract_recall_events.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone, date
from pathlib import Path
from collections import defaultdict

TRACES_ROOT = Path.home() / "Data" / "0-personal" / "traces" / "claude-code"
OBSERVATIONS_DIR = Path.home() / ".plur" / "observations"

RECALL_TOOLS = {
    # MCP-namespaced (Claude Code style)
    "mcp__plur__plur_recall",
    "mcp__plur__plur_recall_hybrid",
    "mcp__plur__plur_inject",
    "mcp__plur__plur_inject_hybrid",
    "mcp__plur__plur_admin",
    "mcp__plur__plur_session_start",
    # Bare names (other clients)
    "plur_recall",
    "plur_recall_hybrid",
    "plur_inject",
    "plur_inject_hybrid",
    "plur_admin",
    "plur_session_start",
}

ENGRAM_ID_PAT = re.compile(r"\b(ENG|ABS|META)-[A-Za-z0-9-]+\b")


def iter_session_lines(path: Path):
    try:
        with path.open() as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    yield json.loads(ln)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def extract_engram_ids_from_result(text: str) -> list[str]:
    """Extract distinct engram IDs from a tool_result body, preserving order."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for m in ENGRAM_ID_PAT.finditer(text):
        eid = m.group(0)
        if eid not in seen_set:
            seen_set.add(eid)
            seen.append(eid)
    return seen


def parse_session(transcript_path: Path) -> list[dict]:
    """Walk a session transcript; emit one record per recall call OR hook injection."""
    session_id = transcript_path.stem  # uuid without .jsonl
    project_dir = transcript_path.parent.name

    out: list[dict] = []
    # Track tool_use → tool_use_id → recall metadata, so we can pair
    # with the tool_result that comes a turn or two later.
    pending: dict[str, dict] = {}

    for turn_idx, msg in enumerate(iter_session_lines(transcript_path)):
        mtype = msg.get("type")
        message = msg.get("message", {})
        # attachment-type messages put the engram text in the top-level "text" field
        content = message.get("content", []) if isinstance(message, dict) else []
        if not isinstance(content, list):
            content = []

        ts = msg.get("timestamp", "")
        cwd = msg.get("cwd", "")

        # Hook injections: PLUR's UserPromptSubmit + command_recall_inject hooks
        # emit additionalContext that Claude Code stores as type="attachment"
        # with attachment.type="hook_success". These are NOT tool calls — they
        # are synthetic context blobs that put engrams directly into the
        # model's context window.
        #
        # We only count attachment messages with attachment.type=="hook_success"
        # AND that contain engram IDs. Other attachment subtypes (skill_listing,
        # task_reminder, edited_text_file, queued_command) may incidentally
        # mention an engram ID in unrelated content — that's a REFERENCE, not
        # an injection. Counting those would inflate the injection count.
        if mtype == "attachment":
            attachment = msg.get("attachment", {})
            att_type = attachment.get("type", "") if isinstance(attachment, dict) else ""
            if att_type != "hook_success":
                continue
            # hook_success attachments put the hook's actual stdout under
            # attachment.stdout (where PLUR's additionalContext payload
            # lands). attachment.content is the structured wrapper used by
            # OTHER attachment kinds (skill_listing, queued_command, etc.).
            # PLUR's UserPromptSubmit hook emits its engram block to stdout,
            # so this is where the engram IDs live for injection events.
            att_stdout = attachment.get("stdout", "") if isinstance(attachment, dict) else ""
            ids = extract_engram_ids_from_result(att_stdout)
            if not ids:
                continue
            att_content = att_stdout  # for subtype classification heuristics below
            # Sub-classify which hook fired. PLUR's session-start injection
            # uses DIRECTIVES/CONSTRAINTS/ALSO CONSIDER headers;
            # command_recall_inject uses "## Relevant memory (engrams)".
            if "Relevant memory" in att_content or "## Memory" in att_content:
                subtype = "hook_command"
            elif "DIRECTIVES" in att_content or "ALSO CONSIDER" in att_content:
                subtype = "hook_session"
            else:
                subtype = "hook_other"
            out.append({
                "ts": ts,
                "session": session_id,
                "session_project": project_dir,
                "source": "hook",
                "source_subtype": subtype,
                "tool": "hook_attachment",
                "query": "",
                "engram_ids": ids,
                "engram_count": len(ids),
                "turn_index": turn_idx,
                "cwd": cwd,
                "scope": None,
            })
            continue

        if mtype == "assistant":
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                if name not in RECALL_TOOLS:
                    continue
                tool_use_id = block.get("id", "")
                args = block.get("input", {}) or {}
                query = (
                    args.get("query")
                    or args.get("task")
                    or args.get("prompt")
                    or ""
                )
                # Tool-source subtype: differentiate explicit recall calls from
                # session_start (auto-fires once) and inject (variant of recall).
                if "session_start" in name:
                    tsubtype = "tool_session_start"
                elif "inject" in name:
                    tsubtype = "tool_inject"
                else:
                    tsubtype = "tool_recall"
                pending[tool_use_id] = {
                    "ts": ts,
                    "session": session_id,
                    "session_project": project_dir,
                    "source": "tool",
                    "source_subtype": tsubtype,
                    "tool": name,
                    "query": (query or "")[:200],
                    "turn_index": turn_idx,
                    "cwd": cwd,
                    "scope": args.get("scope"),
                }

        elif mtype == "user":
            # tool_result blocks land in user messages
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    continue
                tool_use_id = block.get("tool_use_id", "")
                pend = pending.pop(tool_use_id, None)
                if not pend:
                    continue
                # tool_result content can be string OR list of {type, text}
                result_content = block.get("content", "")
                if isinstance(result_content, list):
                    result_str = " ".join(
                        (b.get("text", "") if isinstance(b, dict) else str(b))
                        for b in result_content
                    )
                else:
                    result_str = str(result_content)
                ids = extract_engram_ids_from_result(result_str)
                rec = {
                    **pend,
                    "engram_ids": ids,
                    "engram_count": len(ids),
                }
                out.append(rec)

    return out


def session_date(transcript_path: Path) -> date:
    return datetime.fromtimestamp(transcript_path.stat().st_mtime, tz=timezone.utc).date()


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--since",   type=str, help="YYYY-MM-DD; only sessions modified on/after this date")
    p.add_argument("--until",   type=str, help="YYYY-MM-DD; only sessions modified on/before this date")
    p.add_argument("--session", type=str, help="Process one specific session uuid")
    p.add_argument("--dry-run", action="store_true", help="Print counts; don't write files")
    p.add_argument("--quiet",   action="store_true", help="Less output")
    args = p.parse_args(argv[1:])

    since = date.fromisoformat(args.since) if args.since else None
    until = date.fromisoformat(args.until) if args.until else None

    if not TRACES_ROOT.exists():
        print(f"error: {TRACES_ROOT} does not exist", file=sys.stderr)
        return 2

    # Gather transcript files
    transcripts: list[Path] = []
    if args.session:
        for tp in TRACES_ROOT.rglob(f"{args.session}*.jsonl"):
            transcripts.append(tp)
    else:
        for tp in TRACES_ROOT.rglob("*.jsonl"):
            d = session_date(tp)
            if since and d < since:
                continue
            if until and d > until:
                continue
            transcripts.append(tp)

    if not transcripts:
        print("no transcripts matched filters", file=sys.stderr)
        return 0

    if not args.quiet:
        print(f"Processing {len(transcripts)} transcript(s)...", file=sys.stderr)

    # Bucket records by date for output sharding
    by_date: dict[date, list[dict]] = defaultdict(list)
    total_calls = 0
    total_engrams = 0
    sessions_with_recall = 0

    for tp in transcripts:
        records = parse_session(tp)
        if records:
            sessions_with_recall += 1
        for rec in records:
            total_calls += 1
            total_engrams += rec["engram_count"]
            # Bucket by record's ts (fall back to session date)
            try:
                d = datetime.fromisoformat(rec["ts"].replace("Z", "+00:00")).date()
            except Exception:
                d = session_date(tp)
            by_date[d].append(rec)

    # Summary
    print(f"\nFOUND {total_calls:,} recall calls across {sessions_with_recall:,} sessions "
          f"({total_engrams:,} engram-recall pairs)", file=sys.stderr)
    for d in sorted(by_date.keys()):
        print(f"  {d.isoformat()}: {len(by_date[d]):4d} calls", file=sys.stderr)

    if args.dry_run:
        print("(dry-run — no files written)", file=sys.stderr)
        return 0

    # Write per-date observations files
    OBSERVATIONS_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for d, recs in sorted(by_date.items()):
        out_path = OBSERVATIONS_DIR / f"recall-{d.isoformat()}.jsonl"
        # Idempotent: dedup by (session, turn_index, source) — last write wins.
        # Tuple includes source so a hook injection + tool recall at the same
        # turn (rare but possible) don't overwrite each other.
        existing: dict[tuple, dict] = {}
        if out_path.exists():
            for ln in out_path.read_text().splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    o = json.loads(ln)
                    key = (o.get("session"), o.get("turn_index"), o.get("source", "tool"))
                    existing[key] = o
                except json.JSONDecodeError:
                    pass
        for rec in recs:
            key = (rec["session"], rec["turn_index"], rec.get("source", "tool"))
            existing[key] = rec
        # Write sorted by ts
        ordered = sorted(existing.values(), key=lambda r: r.get("ts", ""))
        with out_path.open("w") as f:
            for rec in ordered:
                f.write(json.dumps(rec) + "\n")
        written += len(recs)

    print(f"\nWROTE {written:,} records to {OBSERVATIONS_DIR}/recall-*.jsonl", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
