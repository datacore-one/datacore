#!/usr/bin/env python3
"""analyze_session.py — produce a per-session ROI scorecard.

For each Claude Code session transcript, summarize:
  - first 3 user messages (the "ask")
  - duration
  - recall calls (plur_recall*/plur_session_start/plur_inject*) + engram IDs returned
  - engrams CREATED in-session (plur_learn calls) + their statements
  - engrams REUSED (recalled ID matches an ID created earlier in the same session or elsewhere)
  - token totals (input/output/cache_read/cache_creation) — proxy for cost
  - tool use frequency (top 10 tools)

USAGE
  python3 analyze_session.py <session-uuid>
  python3 analyze_session.py --top 3                  # pick the 3 most active by size
  python3 analyze_session.py --uuids a,b,c            # comma-separated list
  python3 analyze_session.py --since 2026-05-15 --top 5

Output is JSON to stdout (one object per session) + a human summary to stderr.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter

TRACES_ROOT = Path.home() / "Data" / "0-personal" / "traces" / "claude-code"

RECALL_TOOLS = {
    "mcp__plur__plur_recall",
    "mcp__plur__plur_recall_hybrid",
    "mcp__plur__plur_inject",
    "mcp__plur__plur_inject_hybrid",
    "mcp__plur__plur_session_start",
    "plur_recall", "plur_recall_hybrid",
    "plur_inject", "plur_inject_hybrid", "plur_session_start",
}
LEARN_TOOLS = {
    "mcp__plur__plur_learn", "mcp__plur__plur_capture",
    "plur_learn", "plur_capture",
}

ENGRAM_ID_PAT = re.compile(r"\b(ENG|ABS|META)-[A-Za-z0-9-]+\b")


def iter_session(path: Path):
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


def find_session(uuid: str) -> Path | None:
    for p in TRACES_ROOT.rglob(f"{uuid}*.jsonl"):
        return p
    return None


def analyze(transcript: Path) -> dict:
    first_user_msgs: list[str] = []
    first_ts: str | None = None
    last_ts: str | None = None
    tool_counter: Counter = Counter()
    input_toks = output_toks = cache_read = cache_creation = 0
    recall_calls = 0
    recall_engrams_returned: list[str] = []
    learn_calls = 0
    learn_statements: list[str] = []
    pending: dict[str, str] = {}  # tool_use_id -> tool name
    msg_count = 0

    for msg in iter_session(transcript):
        msg_count += 1
        ts = msg.get("timestamp", "")
        if ts:
            if first_ts is None:
                first_ts = ts
            last_ts = ts

        mtype = msg.get("type")
        message = msg.get("message", {}) if isinstance(msg.get("message"), dict) else {}
        content = message.get("content", [])

        if mtype == "user":
            # Capture first 3 NON-tool-result user messages
            if isinstance(content, str):
                txt = content.strip()
                if txt and len(first_user_msgs) < 3 and not txt.startswith("<system-"):
                    first_user_msgs.append(txt[:300])
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict):
                        if b.get("type") == "tool_result":
                            # pair with pending recall/learn
                            tid = b.get("tool_use_id", "")
                            name = pending.pop(tid, None)
                            if name in RECALL_TOOLS:
                                result_content = b.get("content", "")
                                if isinstance(result_content, list):
                                    rstr = " ".join(
                                        (x.get("text", "") if isinstance(x, dict) else str(x))
                                        for x in result_content
                                    )
                                else:
                                    rstr = str(result_content)
                                for m in ENGRAM_ID_PAT.finditer(rstr):
                                    recall_engrams_returned.append(m.group(0))
                        elif b.get("type") == "text" and len(first_user_msgs) < 3:
                            txt = b.get("text", "").strip()
                            if txt and not txt.startswith("<system-"):
                                first_user_msgs.append(txt[:300])

        elif mtype == "assistant":
            usage = message.get("usage", {})
            input_toks   += int(usage.get("input_tokens", 0) or 0)
            output_toks  += int(usage.get("output_tokens", 0) or 0)
            cache_read   += int(usage.get("cache_read_input_tokens", 0) or 0)
            cache_creation += int(usage.get("cache_creation_input_tokens", 0) or 0)
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") != "tool_use":
                        continue
                    name = b.get("name", "")
                    tool_counter[name] += 1
                    if name in RECALL_TOOLS:
                        recall_calls += 1
                        pending[b.get("id", "")] = name
                    elif name in LEARN_TOOLS:
                        learn_calls += 1
                        pending[b.get("id", "")] = name
                        inp = b.get("input", {}) or {}
                        stmt = inp.get("statement") or inp.get("content") or inp.get("text") or ""
                        if stmt:
                            learn_statements.append(stmt[:160])

    # Duration
    duration_min = None
    if first_ts and last_ts:
        try:
            t0 = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            duration_min = round((t1 - t0).total_seconds() / 60.0, 1)
        except Exception:
            pass

    distinct_recalled = list({eid for eid in recall_engrams_returned})

    return {
        "session": transcript.stem,
        "project": transcript.parent.name,
        "path": str(transcript),
        "size_mb": round(transcript.stat().st_size / 1024 / 1024, 1),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "duration_min": duration_min,
        "msg_count": msg_count,
        "first_user_messages": first_user_msgs,
        "tokens": {
            "input": input_toks,
            "output": output_toks,
            "cache_read": cache_read,
            "cache_creation": cache_creation,
            "billable_proxy": output_toks + cache_creation,  # what we actually pay for
        },
        "recall_calls": recall_calls,
        "recall_engrams_returned_total": len(recall_engrams_returned),
        "recall_engrams_distinct": len(distinct_recalled),
        "engrams_recalled_distinct_ids": distinct_recalled[:30],
        "learn_calls": learn_calls,
        "learn_statements_sample": learn_statements[:10],
        "top_tools": tool_counter.most_common(10),
    }


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument("uuid", nargs="?", help="One session uuid")
    g.add_argument("--uuids", help="Comma-separated session uuids")
    g.add_argument("--top",   type=int, help="Pick the TOP-N most active (by size_mb)")
    p.add_argument("--since", type=str, help="YYYY-MM-DD (for --top)")
    p.add_argument("--json-only", action="store_true")
    args = p.parse_args(argv[1:])

    paths: list[Path] = []
    if args.uuid:
        path = find_session(args.uuid)
        if path:
            paths.append(path)
    elif args.uuids:
        for u in args.uuids.split(","):
            u = u.strip()
            if not u:
                continue
            path = find_session(u)
            if path:
                paths.append(path)
    elif args.top:
        cands = []
        since = datetime.fromisoformat(args.since).date() if args.since else None
        for tp in TRACES_ROOT.rglob("*.jsonl"):
            d = datetime.fromtimestamp(tp.stat().st_mtime, tz=timezone.utc).date()
            if since and d < since:
                continue
            cands.append(tp)
        cands.sort(key=lambda x: x.stat().st_size, reverse=True)
        paths = cands[:args.top]
    else:
        print("error: provide UUID, --uuids, or --top N", file=sys.stderr)
        return 2

    if not paths:
        print("error: no matching session(s)", file=sys.stderr)
        return 2

    results = []
    for path in paths:
        if not args.json_only:
            print(f"Analyzing {path.name} ({path.stat().st_size // 1024 // 1024}M)...", file=sys.stderr)
        r = analyze(path)
        results.append(r)
        if not args.json_only:
            print_summary(r)

    print(json.dumps(results, indent=2))
    return 0


def print_summary(r: dict) -> None:
    print("\n" + "=" * 70, file=sys.stderr)
    print(f"SESSION {r['session'][:8]}  ({r['project']})", file=sys.stderr)
    print(f"  size:       {r['size_mb']} MB, {r['msg_count']} messages", file=sys.stderr)
    print(f"  duration:   {r['duration_min']} min   ({r['first_ts']} → {r['last_ts']})", file=sys.stderr)
    print(f"  tokens:     output={r['tokens']['output']:,}  cache_creation={r['tokens']['cache_creation']:,}  "
          f"cache_read={r['tokens']['cache_read']:,}  input={r['tokens']['input']:,}", file=sys.stderr)
    print(f"  billable:   {r['tokens']['billable_proxy']:,} (output + cache_creation)", file=sys.stderr)
    print(f"  recalls:    {r['recall_calls']} calls, {r['recall_engrams_returned_total']} engrams returned "
          f"({r['recall_engrams_distinct']} distinct)", file=sys.stderr)
    print(f"  learns:     {r['learn_calls']} new engrams captured in this session", file=sys.stderr)
    if r["first_user_messages"]:
        print(f"  ask:        {r['first_user_messages'][0][:150]}", file=sys.stderr)
        for um in r["first_user_messages"][1:]:
            print(f"              {um[:150]}", file=sys.stderr)
    if r["top_tools"]:
        print(f"  top tools:  {', '.join(f'{n}×{c}' for n, c in r['top_tools'][:5])}", file=sys.stderr)
    if r["learn_statements_sample"]:
        print("  learned:", file=sys.stderr)
        for s in r["learn_statements_sample"][:5]:
            print(f"     - {s}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
