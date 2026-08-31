#!/usr/bin/env python3
"""Install a freshly minted PLUR remote-store token into ~/.plur/config.yaml.

WHY THIS EXISTS. The token for a PLUR remote is not stored once. It is copied
into every `stores:` entry that points at that host — 12 entries for
plur.datafund.io as of 2026-08-31. Hand-editing them is the failure mode: one
missed entry leaves that scope 401ing silently while `plur_doctor` reports the
host as authenticated, because the doctor probes one entry and not all of them.

`creds.py adopt-token` cannot do this — it only handles a `json:` store and
writes a `claudeAiOauth` block. This is the YAML multi-entry equivalent, built
on the same principles:

  - STDIN ONLY. Never argv: argv lands in shell history and in `ps` output.
  - BACK UP FIRST. The overwrite is irreversible; the old value cannot be
    re-derived, only re-minted through an interactive browser login.
  - NEVER PRINT A VALUE. Fingerprints (truncated sha256) only, so a terminal
    log or a screen-share cannot leak the credential.
  - REFUSE A STALE PASTE. Decodes the JWT `exp` and refuses a token that is
    already expired — the most likely mistake is re-pasting the dead one.

Usage:

    # mint at https://<host>/me/api-keys first, then:
    python3 .datacore/lib/plur_token_refresh.py --host plur.datafund.io
    # paste the token, Ctrl-D

    # see what would change without writing:
    python3 .datacore/lib/plur_token_refresh.py --host plur.datafund.io --dry-run

Then restart the MCP server so it reloads config, and flush the outbox.

Written 2026-08-31 after the plur.datafund.io token expired 2026-08-28 and six
team-scoped engram writes queued behind it.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

CONFIG = Path("~/.plur/config.yaml").expanduser()
TOKEN_CHARS = re.compile(r"^[A-Za-z0-9._\-]+$")


def fingerprint(value: str | None) -> str:
    if not value:
        return "<unset>"
    return hashlib.sha256(str(value).encode()).hexdigest()[:12]


def jwt_expiry(token: str) -> dt.datetime | None:
    """The `exp` claim, or None if this is not a decodable JWT.

    Decoding proves structure and expiry only. It proves nothing about whether
    the server accepts the token — that is what the post-write check is for.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return dt.datetime.fromtimestamp(exp, dt.UTC)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True,
                    help="hostname whose store entries to update, e.g. plur.datafund.io")
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change; write nothing")
    args = ap.parse_args()

    if not args.config.exists():
        print(f"no config at {args.config}", file=sys.stderr)
        return 1

    raw = args.config.read_text()
    # safe_dump does not preserve comments. The config had none on 2026-08-31,
    # but refuse rather than silently discard them if that ever changes.
    comments = [l for l in raw.splitlines() if l.lstrip().startswith("#")]
    if comments:
        print(f"  REFUSED: {args.config} contains {len(comments)} comment "
              f"line(s), which a YAML round-trip would discard. Edit the token "
              f"by hand, or switch this script to ruamel.yaml.", file=sys.stderr)
        return 1
    data = yaml.safe_load(raw) or {}
    stores = data.get("stores") or []

    targets = [
        (i, s) for i, s in enumerate(stores)
        if args.host in str(s.get("url") or "")
    ]
    if not targets:
        print(f"no store entries reference {args.host}", file=sys.stderr)
        return 1

    current = {fingerprint(s.get("token")) for _, s in targets}
    print(f"  {len(targets)} store entries reference {args.host}", file=sys.stderr)
    print(f"  current token fingerprint(s): {', '.join(sorted(current))}", file=sys.stderr)
    for _, s in targets:
        exp = jwt_expiry(str(s.get("token") or ""))
        if exp:
            state = "EXPIRED" if exp < dt.datetime.now(dt.UTC) else "valid"
            print(f"  current expiry: {exp.isoformat()}  [{state}]", file=sys.stderr)
            break

    if sys.stdin.isatty():
        print(f"\n  paste the new token from https://{args.host}/me/api-keys, "
              f"then Ctrl-D:", file=sys.stderr)
    token = sys.stdin.read().strip()
    if not token:
        print("no token on stdin", file=sys.stderr)
        return 2
    if not TOKEN_CHARS.match(token):
        print("that does not look like a token (unexpected characters)", file=sys.stderr)
        return 2

    exp = jwt_expiry(token)
    now = dt.datetime.now(dt.UTC)
    if exp is None:
        print("  note: not a decodable JWT — cannot check expiry, continuing", file=sys.stderr)
    elif exp < now:
        print(f"  REFUSED: that token expired {exp.isoformat()} "
              f"({(now - exp).days}d ago). It is probably the one you are "
              f"replacing — mint a new one.", file=sys.stderr)
        return 1
    else:
        print(f"  new token expires {exp.isoformat()} "
              f"(in {(exp - now).days}d)", file=sys.stderr)

    if fingerprint(token) in current:
        print("  REFUSED: that is byte-identical to the token already in place.",
              file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"\n  --dry-run: would rewrite {len(targets)} entries "
              f"-> {fingerprint(token)}; nothing written", file=sys.stderr)
        return 0

    backup = args.config.with_suffix(
        args.config.suffix + f".bak-token-{now:%Y%m%d%H%M%S}")
    shutil.copy2(args.config, backup)
    os.chmod(backup, 0o600)
    print(f"\n  previous config saved to {backup}", file=sys.stderr)

    for i, _ in targets:
        stores[i]["token"] = token
    data["stores"] = stores

    tmp = args.config.with_suffix(args.config.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    os.chmod(tmp, 0o600)
    tmp.replace(args.config)

    check = yaml.safe_load(args.config.read_text()) or {}
    rewritten = sum(
        1 for s in (check.get("stores") or [])
        if args.host in str(s.get("url") or "")
        and fingerprint(s.get("token")) == fingerprint(token)
    )
    print(f"  rewrote {rewritten}/{len(targets)} entries "
          f"-> {fingerprint(token)}  {args.config}", file=sys.stderr)
    if rewritten != len(targets):
        print(f"  WARNING: {len(targets) - rewritten} entries did not take. "
              f"Restore from {backup} and investigate.", file=sys.stderr)
        return 1

    print("\n  next:", file=sys.stderr)
    print("    1. restart the MCP server so it reloads config", file=sys.stderr)
    print("    2. verify:  plur_doctor  (expect the host to report ok)", file=sys.stderr)
    print("    3. flush:   plur_outbox with flush:true", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
