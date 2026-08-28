#!/usr/bin/env python3
"""transcript_user_turns.py — dump the human turns out of an archived session transcript.

Learning extraction (session_learning_sweep.py, /wrap-up) only cares about what
the USER said: corrections, preferences, and the occasional "no, do it this way".
Assistant prose and tool payloads are 95%+ of a transcript's bytes and almost
none of its evidence, so this strips them.

Filtered out: tool_result blocks, hook/system-reminder injections, command
stdout wrappers, and the local-command-stdout echo. What remains is the text
the human actually typed.

USAGE
  python3 transcript_user_turns.py <path-to-transcript.jsonl[.gz]> [--max-chars N]
  python3 transcript_user_turns.py --day 2026-08-17 [--uuids a,b,c]

Output: plain text, one "--- [n] ---" separated block per user turn.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path

ARCHIVE_ROOT = Path.home() / "Data" / ".datacore" / "state" / "sessions" / "archive"

# Turns that are machinery, not the human speaking.
NOISE_PREFIXES = (
    "<command-name>",
    "<local-command-stdout>",
    "Caveat: The messages below",
    "[Request interrupted",
)
NOISE_PATTERNS = (
    re.compile(r"<system-reminder>.*?</system-reminder>", re.S),
    re.compile(r"<command-message>.*?</command-message>", re.S),
)


def _open(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def user_turns(path: Path, max_chars: int = 4000):
    """Yield the human-authored text of each user turn in order."""
    with _open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "user":
                continue
            msg = rec.get("message") or {}
            content = msg.get("content")
            parts = []
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    # tool_result blocks are deliberately dropped
            text = "\n".join(p for p in parts if p).strip()
            for pat in NOISE_PATTERNS:
                text = pat.sub("", text).strip()
            if not text:
                continue
            if text.startswith(NOISE_PREFIXES):
                continue
            yield text[:max_chars]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript", nargs="?", help="path to transcript.jsonl[.gz]")
    ap.add_argument("--day", help="YYYY-MM-DD under the session archive root")
    ap.add_argument("--uuids", help="comma-separated session uuid prefixes to include")
    ap.add_argument("--max-chars", type=int, default=4000)
    args = ap.parse_args(argv)

    targets: list[tuple[str, Path]] = []
    if args.transcript:
        targets.append((Path(args.transcript).parent.name, Path(args.transcript)))
    elif args.day:
        wanted = [u.strip() for u in args.uuids.split(",")] if args.uuids else None
        for d in sorted((ARCHIVE_ROOT / args.day).iterdir()):
            if wanted and not any(d.name.startswith(w) for w in wanted):
                continue
            t = d / "transcript.jsonl.gz"
            if t.exists():
                targets.append((d.name, t))
    else:
        ap.error("give a transcript path or --day")

    for name, path in targets:
        print(f"\n########## SESSION {name} ##########")
        for i, turn in enumerate(user_turns(path, args.max_chars), 1):
            print(f"--- [{i}] ---")
            print(turn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
