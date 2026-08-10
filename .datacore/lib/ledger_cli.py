#!/usr/bin/env python3
"""ledger_cli.py — operator surface for the event-ledger substrate.

Thin argparse wrapper around the `ledger` package: appends events, verifies
per-writer hash chains, folds the log into item/spend state, and exposes
that state as JSON (directly, or via the disposable SQLite index).

Usage:
    python3 ledger_cli.py append --space <dir> --type <t> --payload '<json>' [--actor <a>]
    python3 ledger_cli.py verify --space <dir> [--strict]
    python3 ledger_cli.py items --space <dir> [--status <s>] [--owner <o>]
    python3 ledger_cli.py balances --space <dir>

Stdout/stderr discipline: every command's DATA (the appended event's
hash/hlc, the "OK ..." summary, item JSON lines, the balances object) goes
to stdout; every DIAGNOSTIC (verify error lines, clean failure messages)
goes to stderr. Expected failures (bad payload JSON, unknown event type, a
missing --space directory, a broken chain) are caught and reported as a
clean one-line stderr message with a nonzero exit code -- never a
traceback. Genuinely unexpected exceptions are allowed to propagate with a
traceback; that is not this script's job to hide.

Actor resolution (append only): `--actor`, else `$DATACORE_ACTOR`, else
`socket.gethostname()`.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger.fold import fold  # noqa: E402
from ledger.index import build_index, items_by  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.verify import verify_chain  # noqa: E402


def _default_actor() -> str:
    return os.environ.get("DATACORE_ACTOR") or socket.gethostname()


def _json_dict(raw: str) -> dict:
    """argparse `type=` for --payload: must parse as a JSON object.

    Raising `argparse.ArgumentTypeError` lets argparse's own error path
    handle it (usage message to stderr, exit code 2) -- no custom exit
    logic needed here.
    """
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"--payload is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("--payload must be a JSON object (dict)")
    return value


def _require_space(space_arg: str) -> Path:
    """Resolve --space to a Path, or fail cleanly (no traceback) if it's not
    an existing directory. Used by verify/items/balances, which read
    existing state; `append` deliberately does NOT use this -- it creates
    the space's event dir on demand (that's EventLog's job)."""
    space = Path(space_arg)
    if not space.is_dir():
        print(f"error: space directory not found: {space}", file=sys.stderr)
        sys.exit(1)
    return space


def cmd_append(args: argparse.Namespace) -> None:
    actor = args.actor or _default_actor()
    log = EventLog(Path(args.space), actor)
    try:
        event = log.append(args.type, args.payload)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"hash": event.hash, "hlc": event.hlc}))


def cmd_verify(args: argparse.Namespace) -> None:
    space = _require_space(args.space)
    events_dir = space / ".datacore" / "events"
    files = sorted(events_dir.glob("*.jsonl")) if events_dir.exists() else []

    had_errors = False
    for path in files:
        for error in verify_chain(path, strict=args.strict):
            print(f"{path.name}: {error}", file=sys.stderr)
            had_errors = True

    if had_errors:
        sys.exit(1)

    total_events = len(read_events(space))
    print(f"OK {len(files)} files {total_events} events")


def cmd_items(args: argparse.Namespace) -> None:
    space = _require_space(args.space)
    state = fold(read_events(space))
    db_path = space / ".datacore" / "state" / "ledger" / "index.db"
    build_index(state, db_path)
    for item in items_by(db_path, status=args.status, owner=args.owner):
        print(json.dumps(item))


def cmd_balances(args: argparse.Namespace) -> None:
    space = _require_space(args.space)
    state = fold(read_events(space))
    print(json.dumps(state.spend))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ledger_cli.py",
        description="Operator CLI over the event-ledger substrate.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("append", help="Append one event to the ledger")
    p.add_argument("--space", required=True, help="Space directory root")
    p.add_argument("--type", required=True, dest="type", help="Event type")
    p.add_argument("--payload", required=True, type=_json_dict, help="Event payload (JSON object)")
    p.add_argument("--actor", default=None, help="Actor id (default: $DATACORE_ACTOR or hostname)")

    p = sub.add_parser("verify", help="Verify every writer's hash chain in a space")
    p.add_argument("--space", required=True, help="Space directory root")
    p.add_argument("--strict", action="store_true", help="Flag unsigned events as errors")

    p = sub.add_parser("items", help="Fold events and list items")
    p.add_argument("--space", required=True, help="Space directory root")
    p.add_argument("--status", default=None, help="Filter by item status")
    p.add_argument("--owner", default=None, help="Filter by item owner")

    p = sub.add_parser("balances", help="Fold events and print per-actor spend")
    p.add_argument("--space", required=True, help="Space directory root")

    return parser


COMMANDS = {
    "append": cmd_append,
    "verify": cmd_verify,
    "items": cmd_items,
    "balances": cmd_balances,
}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    COMMANDS[args.command](args)


if __name__ == "__main__":
    main()
