#!/usr/bin/env python3
"""find_failed_recalls.py — identify moments where recall failed and a new
engram was created shortly after, with the token cost between them.

DEFINITION
  A "failed recall" pattern is one of:
    Type A (NEAR-MISS): plur_recall* was called, the result did NOT contain
       an engram whose statement closely matches a plur_learn call made N
       turns later in the same session. The model paid for rediscovery.
    Type B (NO-RECALL): plur_learn was called without ANY preceding recall
       call in the same session. The model never even asked memory.

OUTPUT
  Per-engram-creation event:
    {
      "session":           "<uuid>",
      "engram_statement":  "first 200 chars",
      "type":              "near-miss" | "no-recall",
      "preceding_recall_turn":   <int or null>,
      "engram_creation_turn":    <int>,
      "rediscovery_tokens":      <int>   # output + cache_creation between
                                          #   recall (or session-start) and learn
      "rediscovery_msg_count":   <int>,
      "creation_context_sample": [first user message in the window]
    }

USAGE
  python3 find_failed_recalls.py <session-uuid>
  python3 find_failed_recalls.py --top 3
  python3 find_failed_recalls.py --uuids a,b,c
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

TRACES_ROOT = Path.home() / "Data" / "0-personal" / "traces" / "claude-code"

RECALL_TOOLS = {
    "mcp__plur__plur_recall", "mcp__plur__plur_recall_hybrid",
    "mcp__plur__plur_inject", "mcp__plur__plur_inject_hybrid",
    "mcp__plur__plur_admin", "mcp__plur__plur_session_start",
    "plur_recall", "plur_recall_hybrid", "plur_inject",
    "plur_inject_hybrid", "plur_admin", "plur_session_start",
}
LEARN_TOOLS = {
    "mcp__plur__plur_learn", "mcp__plur__plur_capture",
    "plur_learn", "plur_capture",
}


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


def keyword_overlap(a: str, b: str) -> float:
    """Jaccard over content-words. Fallback if embeddings unavailable."""
    def tok(s: str) -> set:
        return {
            w for w in re.findall(r"[A-Za-z]{4,}", (s or "").lower())
            if w not in STOP
        }
    ta, tb = tok(a), tok(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


STOP = {
    "this","that","with","from","into","then","than","when","what","which",
    "your","yours","their","them","they","were","have","been","does","done",
    "will","would","could","should","there","these","those","just","also",
    "about","other","some","more","most","like","such","because","using",
    "before","after","without","while","make","made","needs","need","note",
}

# Embedding model — lazy-loaded, shared across calls in one run.
_EMBEDDER = None


def get_embedder():
    """Load sentence-transformers all-MiniLM-L6-v2 once. None on failure."""
    global _EMBEDDER
    if _EMBEDDER is False:
        return None
    if _EMBEDDER is None:
        try:
            from sentence_transformers import SentenceTransformer
            _EMBEDDER = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception as e:
            print(f"warn: embeddings unavailable ({e}); falling back to Jaccard",
                  file=sys.stderr)
            _EMBEDDER = False
            return None
    return _EMBEDDER


def semantic_overlap(a: str, b: str) -> float:
    """Cosine similarity over MiniLM embeddings; falls back to Jaccard."""
    emb = get_embedder()
    if emb is None:
        return keyword_overlap(a, b)
    try:
        # Truncate the recall blob: too long inflates cost without changing
        # signal much (engram statements rarely match passages > 2KB anyway).
        a = (a or "")[:1500]
        b = (b or "")[:8000]
        if not a.strip() or not b.strip():
            return 0.0
        va, vb = emb.encode([a, b], convert_to_numpy=True, normalize_embeddings=True)
        return float(va @ vb)  # cosine since normalized
    except Exception as e:
        print(f"warn: embed failed ({e}); falling back to Jaccard", file=sys.stderr)
        return keyword_overlap(a, b)


def analyze(transcript: Path) -> list[dict]:
    """Walk the session; for each plur_learn, decide near-miss vs covered.

    "Recall" sources considered (both inject engrams into the model's context):
      - Explicit plur_recall* / plur_session_start tool_results.
      - PLUR hook injections — attachment.type='hook_success' with engrams
        in attachment.stdout. These fire on every UserPromptSubmit and are
        the dominant injection path (~85% of all injections).

    Without including hook injections, classification was misleading
    (artificially 100% near-miss) because the model HAD engrams in context
    from hooks, just not from explicit tool calls.
    """
    findings: list[dict] = []
    running_tokens = 0  # output + cache_creation
    running_msgs = 0
    last_recall: dict | None = None
    last_anchor_tokens: int = 0
    last_anchor_msgs:   int = 0
    pending: dict[str, tuple[str, dict]] = {}
    recent_user_msg = ""

    for turn_idx, msg in enumerate(iter_session(transcript)):
        running_msgs += 1
        mtype = msg.get("type")
        message = msg.get("message", {}) if isinstance(msg.get("message"), dict) else {}
        content = message.get("content", [])

        # Hook injection: treat attachment.stdout content as a "recall result"
        # so subsequent plur_learn calls have something to compare against.
        if mtype == "attachment":
            att = msg.get("attachment", {})
            if isinstance(att, dict) and att.get("type") == "hook_success":
                stdout = att.get("stdout", "") or ""
                if "ENG-" in stdout or "ABS-" in stdout or "META-" in stdout:
                    last_recall = {
                        "turn": turn_idx,
                        "tokens_at": running_tokens,
                        "result_blob": stdout,
                        "query": "hook:UserPromptSubmit",
                    }
                    last_anchor_tokens = running_tokens
                    last_anchor_msgs   = running_msgs
            continue

        # Count tokens on assistant turns
        if mtype == "assistant":
            usage = message.get("usage", {})
            running_tokens += int(usage.get("output_tokens", 0) or 0)
            running_tokens += int(usage.get("cache_creation_input_tokens", 0) or 0)

            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") != "tool_use":
                        continue
                    name = b.get("name", "")
                    tid = b.get("id", "")
                    if name in RECALL_TOOLS:
                        pending[tid] = (name, b.get("input", {}) or {})
                    elif name in LEARN_TOOLS:
                        inp = b.get("input", {}) or {}
                        statement = (
                            inp.get("statement") or inp.get("content") or inp.get("text") or ""
                        )
                        if not statement:
                            continue
                        finding = classify_learn(
                            statement=statement,
                            last_recall=last_recall,
                            current_tokens=running_tokens,
                            anchor_tokens=last_anchor_tokens,
                            current_turn=turn_idx,
                            current_msgs=running_msgs,
                            anchor_msgs=last_anchor_msgs,
                            recent_user_msg=recent_user_msg,
                        )
                        finding["session"] = transcript.stem
                        findings.append(finding)
                        # Reset anchor: any subsequent learn measures from HERE
                        last_anchor_tokens = running_tokens
                        last_anchor_msgs   = running_msgs

        elif mtype == "user":
            # tool_result blocks pair with pending recalls
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") != "tool_result":
                        continue
                    tid = b.get("tool_use_id", "")
                    pend = pending.pop(tid, None)
                    if not pend:
                        continue
                    pname, pinput = pend
                    if pname in RECALL_TOOLS:
                        result_content = b.get("content", "")
                        if isinstance(result_content, list):
                            rstr = " ".join(
                                (x.get("text", "") if isinstance(x, dict) else str(x))
                                for x in result_content
                            )
                        else:
                            rstr = str(result_content)
                        last_recall = {
                            "turn": turn_idx,
                            "tokens_at": running_tokens,
                            "result_blob": rstr,
                            "query": pinput.get("query") or pinput.get("task") or "",
                        }
                        # Reset anchor on recall too — a fresh recall is a
                        # fresh chance for the model to know the answer.
                        last_anchor_tokens = running_tokens
                        last_anchor_msgs   = running_msgs
                    elif b.get("type") == "tool_result":
                        pass
            elif isinstance(content, str):
                if content and not content.startswith("<system-"):
                    recent_user_msg = content[:200]

    return findings


REDISCOVERY_CAP = 50_000  # The realistic window in which an engram is "discovered":
                          # ~50K tokens ≈ 5–10 minutes of active model work. Without this
                          # cap, long gaps between learns (where the model does unrelated
                          # tasks) get charged to one engram and inflate by 10×.


def classify_learn(*, statement: str, last_recall: dict | None,
                   current_tokens: int, anchor_tokens: int,
                   current_turn: int, current_msgs: int, anchor_msgs: int,
                   recent_user_msg: str) -> dict:
    raw_spent   = max(0, current_tokens - anchor_tokens)
    spent_tokens = min(raw_spent, REDISCOVERY_CAP)
    spent_msgs   = max(0, current_msgs - anchor_msgs)
    if last_recall is None:
        return {
            "type": "no-recall",
            "engram_statement": statement[:200],
            "preceding_recall_turn": None,
            "engram_creation_turn": current_turn,
            "rediscovery_tokens": spent_tokens,
            "rediscovery_tokens_uncapped": raw_spent,
            "rediscovery_msg_count": spent_msgs,
            "creation_context_sample": recent_user_msg,
        }
    overlap = semantic_overlap(statement, last_recall["result_blob"])
    # Threshold tuned for MiniLM cosine: 0.35 separates "topically related"
    # from "covered the same concept". Lower than Jaccard's 0.10 because
    # cosine on this model ranges narrower.
    return {
        "type": "near-miss" if overlap < 0.35 else "covered",
        "overlap_with_recall": round(overlap, 3),
        "similarity_metric": "cosine" if get_embedder() is not None else "jaccard",
        "engram_statement": statement[:200],
        "preceding_recall_turn": last_recall["turn"],
        "engram_creation_turn": current_turn,
        "rediscovery_tokens": spent_tokens,
        "rediscovery_tokens_uncapped": raw_spent,
        "rediscovery_msg_count": spent_msgs,
        "recall_query": (last_recall.get("query") or "")[:120],
        "creation_context_sample": recent_user_msg,
    }


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument("uuid", nargs="?")
    g.add_argument("--uuids", help="Comma-separated")
    g.add_argument("--top", type=int, help="N most active sessions")
    p.add_argument("--since", type=str)
    args = p.parse_args(argv[1:])

    paths: list[Path] = []
    if args.uuid:
        p2 = find_session(args.uuid)
        if p2:
            paths.append(p2)
    elif args.uuids:
        for u in args.uuids.split(","):
            u = u.strip()
            if not u:
                continue
            p2 = find_session(u)
            if p2:
                paths.append(p2)
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

    all_findings: list[dict] = []
    for path in paths:
        f = analyze(path)
        all_findings.extend(f)
        # Per-session summary to stderr
        by_type = {"near-miss": 0, "no-recall": 0, "covered": 0}
        rediscovery_total = 0
        for x in f:
            by_type[x["type"]] = by_type.get(x["type"], 0) + 1
            if x.get("rediscovery_tokens"):
                rediscovery_total += x["rediscovery_tokens"]
        print(f"\n{path.name[:36]}", file=sys.stderr)
        print(f"  learns:        {len(f)}", file=sys.stderr)
        print(f"  near-miss:     {by_type['near-miss']}  (recall happened but missed)", file=sys.stderr)
        print(f"  no-recall:     {by_type['no-recall']}  (no recall before learn)", file=sys.stderr)
        print(f"  covered:       {by_type['covered']}  (recall covered it but learn ran anyway)", file=sys.stderr)
        print(f"  rediscovery tokens (near-miss + covered): {rediscovery_total:,}", file=sys.stderr)

    print(json.dumps(all_findings, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
