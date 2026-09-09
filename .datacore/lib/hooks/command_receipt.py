#!/usr/bin/env python3
"""Record that a command was actually invoked, so nobody has to take my word.

WHY THIS EXISTS. On 2026-09-09 the owner asked whether the weekly plan had been
produced with the saved `/weekly-plan` method. The honest answer was no — it had
been improvised — but nothing in the repository could have shown that. The only
witness was the agent, and the agent's first answer was wrong. He had to read
the output and recognise it as worse before the truth surfaced.

Nightshift writes an execution record for every task it runs. Commands wrote
nothing. So "did you follow the process?" was answerable only by asking the
party with an interest in the answer.

This hook fires on the harness's Skill / SlashCommand invocation, before the
agent does anything, and appends one line per invocation. The agent cannot skip
it, because the agent does not call it. A command with no receipt was not run.

    command_receipt.py                      # hook mode: reads hook JSON on stdin
    command_receipt.py --check weekly-plan  # was it run today? exit 1 if not
    command_receipt.py --check weekly-plan --date 2026-09-08
    command_receipt.py --list [--date D]    # everything invoked that day

Fail-open as a hook: any error exits 0 and blocks nothing. A missing receipt is
a weaker signal than a blocked command.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

STATE = Path.home() / ".datacore" / "state" / "command-runs"


def _log(day: str) -> Path:
    return STATE / f"{day}.jsonl"


def record(payload: dict) -> None:
    tool = str(payload.get("tool_name") or payload.get("toolName") or "")
    ti = payload.get("tool_input") or payload.get("toolInput") or {}
    name = str(ti.get("skill") or ti.get("command") or ti.get("name") or "").strip()
    if not name:
        return
    name = name.lstrip("/")
    now = datetime.now(timezone.utc)
    STATE.mkdir(parents=True, exist_ok=True)
    row = {
        "command": name,
        "tool": tool,
        "at": now.isoformat(),
        "session": os.environ.get("CLAUDE_SESSION_ID", ""),
        "args": str(ti.get("args") or "")[:200],
    }
    with _log(now.date().isoformat()).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def rows(day: str) -> list[dict]:
    f = _log(day)
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", metavar="COMMAND")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--date", default=date.today().isoformat())
    args, _unknown = ap.parse_known_args()

    if args.check:
        want = args.check.lstrip("/")
        hits = [r for r in rows(args.date) if r.get("command", "").lstrip("/") == want]
        if hits:
            print(f"/{want} invoked {len(hits)}x on {args.date}: "
                  + ", ".join(h["at"][11:19] for h in hits))
            return 0
        print(f"/{want} was NOT invoked on {args.date}. "
              f"Work claiming to follow it was not produced by it.")
        return 1

    if args.list:
        rs = rows(args.date)
        if not rs:
            print(f"no commands invoked on {args.date}")
            return 0
        print(f"{len(rs)} invocation(s) on {args.date}:")
        for r in rs:
            print(f"  {r['at'][11:19]}  /{r['command']}"
                  + (f"  {r['args'][:60]}" if r.get("args") else ""))
        return 0

    try:
        record(json.load(sys.stdin))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
