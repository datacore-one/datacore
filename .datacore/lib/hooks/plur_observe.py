#!/usr/bin/env python3
"""Capture private event/tool/outcome metadata, without tool inputs or errors.

Observations are best-effort diagnostics. Failures produce a content-free
warning and an empty hook response. Raw tool payloads are never echoed.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import observation_metadata

OBS_DIR = observation_metadata.directory()
MAX_STDIN = 512 * 1024


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--post', action='store_true')
    group.add_argument('--failure', action='store_true')
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.read(MAX_STDIN + 1)
        if len(raw) > MAX_STDIN:
            raise ValueError('oversized hook input')
        data = json.loads(raw) if raw.strip() else {}
        event = ('PostToolUseFailure' if args.failure else 'PostToolUse' if args.post
                 else os.environ.get('CLAUDE_HOOK_EVENT_NAME', 'PreToolUse'))
        value = observation_metadata.record(data, event, os.environ.get('CLAUDE_SESSION_ID', ''), Path.cwd())
        if value is not None:
            observation_metadata.append(OBS_DIR, value)
    except (OSError, ValueError, RecursionError):
        print('Observation metadata unavailable', file=sys.stderr)
    print('{}')


if __name__ == '__main__':
    main()
