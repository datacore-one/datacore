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

This best-effort hook records Skill / SlashCommand invocations before execution.
A receipt is evidence of invocation, not completion. Missing receipts do not
prove non-execution: the hook can fail or an uninstrumented runtime can run work.
It is not an independent security boundary against same-user code.

    command_receipt.py                      # hook mode: reads hook JSON on stdin
    command_receipt.py --check weekly-plan  # was it run today? exit 1 if not
    command_receipt.py --check weekly-plan --date 2026-09-08
    command_receipt.py --list [--date D]    # everything invoked that day

Fail-open as a hook: any error exits 0 and blocks nothing. A missing receipt is
a weaker signal than a blocked command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from file_utils import atomic_write_text, file_lock

STATE = Path(os.environ.get('DATACORE_STATE', Path.home() / '.datacore/state')) / 'command-runs'


def _log(day: str) -> Path:
    day = date.fromisoformat(day).isoformat()
    path = STATE / f"{day}.jsonl"
    if STATE.is_symlink() or path.is_symlink():
        raise ValueError('receipt state cannot be a symbolic link')
    return path


def _read(day: str) -> list[dict]:
    try:
        text = _log(day).read_text(encoding='utf-8')
    except FileNotFoundError:
        return []
    result = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not isinstance(row.get('command'), str) or not isinstance(row.get('at'), str):
            raise ValueError('invalid command receipt')
        stamp = datetime.fromisoformat(row['at'])
        if stamp.tzinfo is None:
            raise ValueError('receipt timestamp has no timezone')
        result.append(row)
    return result


def record(payload: dict) -> None:
    tool = str(payload.get("tool_name") or payload.get("toolName") or "")
    if tool not in {'Skill', 'SlashCommand'}:
        return
    ti = payload.get("tool_input") or payload.get("toolInput") or {}
    name = str(ti.get("skill") or ti.get("command") or ti.get("name") or "").strip()
    if not name:
        return
    name = name.lstrip("/")
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}', name):
        raise ValueError('invalid command name')
    now = datetime.now(timezone.utc)
    path = _log(now.date().isoformat())
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if STATE.stat().st_uid != os.getuid():
        raise ValueError('receipt directory must be owned by the current user')
    STATE.chmod(0o700)
    row = {
        "command": name,
        "tool": tool,
        "at": now.isoformat(),
        "session_sha256": hashlib.sha256(os.environ.get('CLAUDE_SESSION_ID', '').encode()).hexdigest(),
        "args_sha256": hashlib.sha256(str(ti.get('args') or '').encode()).hexdigest(),
    }
    with file_lock(path):
        _read(now.date().isoformat())  # malformed history is never overwritten
        try:
            previous = path.read_text(encoding='utf-8')
        except FileNotFoundError:
            previous = ''
        atomic_write_text(path, previous + ('\n' if previous and not previous.endswith('\n') else '') + json.dumps(row) + '\n')


def rows(day: str) -> list[dict]:
    # Preserve the existing UTC partitions; select the requested LOCAL day by
    # timestamp, including UTC partitions on either side of local midnight.
    target = date.fromisoformat(day)
    out = [row for offset in (-1, 0, 1)
           for row in _read((target + timedelta(days=offset)).isoformat())
           if datetime.fromisoformat(row['at']).astimezone().date() == target]
    return sorted(out, key=lambda row: datetime.fromisoformat(row['at']))


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
                  + ", ".join(datetime.fromisoformat(h['at']).astimezone().strftime('%H:%M:%S') for h in hits))
            return 0
        print(f"No invocation receipt for /{want} on {args.date}.")
        return 1

    if args.list:
        rs = rows(args.date)
        if not rs:
            print(f"no commands invoked on {args.date}")
            return 0
        print(f"{len(rs)} invocation(s) on {args.date}:")
        for r in rs:
            print(f"  {datetime.fromisoformat(r['at']).astimezone().strftime('%H:%M:%S')}  /{r['command']}")
        return 0

    try:
        record(json.load(sys.stdin))
    except Exception:
        print('command receipt could not be recorded', file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print('command receipt query failed; history may require recovery', file=sys.stderr)
        sys.exit(2)
