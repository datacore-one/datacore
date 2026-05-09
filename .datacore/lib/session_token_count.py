#!/usr/bin/env python3
"""
session_token_count.py — count tokens for a Claude Code session by reading
its transcript JSONL and summing the usage objects. No estimation; the API
returns exact counts per turn.

Usage:
  # Most recent session (default — what /wrap-up wants)
  python3 .datacore/lib/session_token_count.py

  # Specific session
  python3 .datacore/lib/session_token_count.py --session <uuid>

  # All sessions today
  python3 .datacore/lib/session_token_count.py --today

  # Pretty JSON for embedding in /wrap-up output
  python3 .datacore/lib/session_token_count.py --json
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from glob import glob
from pathlib import Path
from datetime import datetime


PROJECTS_DIR = Path.home() / '.claude' / 'projects'


@dataclass
class TokenTotals:
    input_tokens:               int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens:    int = 0
    output_tokens:              int = 0
    turns:                      int = 0
    web_search_requests:        int = 0
    web_fetch_requests:         int = 0

    @property
    def total_uncached(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def total_input_processed(self) -> int:
        """All input tokens including cache hits — what the API actually saw."""
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens

    @property
    def total_billable(self) -> int:
        """Approximate billable: cache reads are 10% cost, cache writes 125%; rough sum."""
        return (
            self.input_tokens
            + int(self.cache_creation_input_tokens * 1.25)
            + int(self.cache_read_input_tokens * 0.10)
            + self.output_tokens
        )


def add_usage(totals: TokenTotals, usage: dict) -> None:
    totals.input_tokens               += usage.get('input_tokens', 0) or 0
    totals.cache_creation_input_tokens += usage.get('cache_creation_input_tokens', 0) or 0
    totals.cache_read_input_tokens    += usage.get('cache_read_input_tokens', 0) or 0
    totals.output_tokens              += usage.get('output_tokens', 0) or 0
    server_tool = usage.get('server_tool_use') or {}
    totals.web_search_requests += server_tool.get('web_search_requests', 0) or 0
    totals.web_fetch_requests  += server_tool.get('web_fetch_requests', 0) or 0


def find_usages(node, found: list) -> None:
    """Walk an arbitrary JSON value and collect every dict named 'usage'."""
    if isinstance(node, dict):
        if 'usage' in node and isinstance(node['usage'], dict):
            found.append(node['usage'])
        for v in node.values():
            find_usages(v, found)
    elif isinstance(node, list):
        for item in node:
            find_usages(item, found)


def count_session(transcript_path: Path) -> TokenTotals:
    totals = TokenTotals()
    with transcript_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            usages: list = []
            find_usages(rec, usages)
            for u in usages:
                add_usage(totals, u)
                totals.turns += 1
    return totals


def latest_transcript() -> Path | None:
    """Return the most recently modified .jsonl across all projects."""
    candidates = list(PROJECTS_DIR.glob('*/*.jsonl'))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def all_today() -> list[Path]:
    today = datetime.now().date()
    out = []
    for p in PROJECTS_DIR.glob('*/*.jsonl'):
        if datetime.fromtimestamp(p.stat().st_mtime).date() == today:
            out.append(p)
    return sorted(out, key=lambda p: p.stat().st_mtime)


def fmt_int(n: int) -> str:
    return f'{n:,}'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', help='Session UUID (basename of .jsonl, no extension)')
    parser.add_argument('--today',   action='store_true', help='Sum all sessions modified today')
    parser.add_argument('--json',    action='store_true', help='Output JSON')
    parser.add_argument('--no-cache', action='store_true', help='Hide cache-detail rows')
    args = parser.parse_args()

    sessions: list[tuple[str, Path]] = []
    if args.today:
        for p in all_today():
            sessions.append((p.stem, p))
    elif args.session:
        match = list(PROJECTS_DIR.glob(f'*/{args.session}.jsonl'))
        if not match:
            print(f'session not found: {args.session}', file=sys.stderr)
            return 1
        sessions.append((args.session, match[0]))
    else:
        # Prefer CLAUDE_SESSION_ID env var if set (hook context, or
        # explicitly exported by /wrap-up). The "latest by mtime"
        # fallback is wrong when multiple sessions are active in
        # parallel — the most recently touched isn't necessarily the
        # calling session.
        env_id = os.environ.get('CLAUDE_SESSION_ID')
        if env_id:
            match = list(PROJECTS_DIR.glob(f'*/{env_id}.jsonl'))
            if match:
                sessions.append((env_id, match[0]))
            else:
                print(f'CLAUDE_SESSION_ID={env_id} but transcript not found', file=sys.stderr)
                return 1
        else:
            latest = latest_transcript()
            if not latest:
                print('no transcripts found', file=sys.stderr)
                return 1
            sessions.append((latest.stem, latest))

    grand = TokenTotals()
    per_session = []
    for sid, p in sessions:
        tot = count_session(p)
        per_session.append((sid, p, tot))
        grand.input_tokens               += tot.input_tokens
        grand.cache_creation_input_tokens += tot.cache_creation_input_tokens
        grand.cache_read_input_tokens    += tot.cache_read_input_tokens
        grand.output_tokens              += tot.output_tokens
        grand.turns                      += tot.turns
        grand.web_search_requests        += tot.web_search_requests
        grand.web_fetch_requests         += tot.web_fetch_requests

    if args.json:
        out = {
            'sessions': [
                {
                    'session_id':                sid,
                    'transcript_path':           str(p),
                    'size_bytes':                p.stat().st_size,
                    **asdict(tot),
                    'total_input_processed':     tot.total_input_processed,
                    'total_billable_estimate':   tot.total_billable,
                }
                for sid, p, tot in per_session
            ],
            'grand_total': {
                **asdict(grand),
                'total_input_processed':   grand.total_input_processed,
                'total_billable_estimate': grand.total_billable,
            },
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

    # Human-readable
    for sid, p, tot in per_session:
        print(f'session {sid}')
        print(f'  transcript:  {p}')
        print(f'  size:        {p.stat().st_size // 1024} KB')
        print(f'  turns:       {fmt_int(tot.turns)}')
        print(f'  input:       {fmt_int(tot.input_tokens)}')
        if not args.no_cache:
            print(f'  cache write: {fmt_int(tot.cache_creation_input_tokens)}')
            print(f'  cache read:  {fmt_int(tot.cache_read_input_tokens)}')
        print(f'  output:      {fmt_int(tot.output_tokens)}')
        print(f'  TOTAL:       {fmt_int(tot.total_input_processed + tot.output_tokens)}  (input + output, all categories)')
        if tot.web_search_requests + tot.web_fetch_requests:
            print(f'  server tools: search={tot.web_search_requests} fetch={tot.web_fetch_requests}')
        print()

    if len(per_session) > 1:
        print('=== GRAND TOTAL ===')
        print(f'  turns:       {fmt_int(grand.turns)}')
        print(f'  input:       {fmt_int(grand.input_tokens)}')
        if not args.no_cache:
            print(f'  cache write: {fmt_int(grand.cache_creation_input_tokens)}')
            print(f'  cache read:  {fmt_int(grand.cache_read_input_tokens)}')
        print(f'  output:      {fmt_int(grand.output_tokens)}')
        print(f'  TOTAL:       {fmt_int(grand.total_input_processed + grand.output_tokens)}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
