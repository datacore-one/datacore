#!/usr/bin/env python3
"""Switch the fleet to a different Claude account in one command.

Weekly usage limits mean swapping accounts is routine, not exceptional. Before
this it was archaeology: find which of four env files is canonical, paste a
token into it by hand, remember which services cache it, restart the right
ones, and hope. That is how a credential ends up in three places with two of
them stale — the exact drift the 2026-08-15 audit found.

    claude_account_switch.py status
    claude_account_switch.py set          # reads the token from stdin
    claude_account_switch.py set --check  # verify only, change nothing

GETTING THE TOKEN. On any machine signed in to the target account:

    claude setup-token

That PRINTS a long-lived token; it does not write `~/.claude/.credentials.json`
(the adapter's own docstring says so, and not knowing it cost real time). Pipe
what it prints into `set`.

WHY STDIN AND NEVER AN ARGUMENT. A token passed as argv lands in shell history,
in `ps` output for every user on the box, and in any process accounting. stdin
leaves it in none of those.

WHAT IT DOES. Writes the token to the canonical store, then delegates
propagation and service restarts to `cos_token_refresh.py`, which already knows
every consumer. This script owns "which account", not "who needs telling" —
duplicating the consumer list is how the two lists drift apart.

VERIFIES AFTER SWITCHING, always. A switch that reports success without a live
call is a claim, and the failure mode this replaces was precisely a credential
that looked fine and was not.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ENV_FILE = os.environ.get("DATACORED_ENV_FILE", "/etc/datacored.env")
TOKEN_KEY = "CLAUDE_CODE_OAUTH_TOKEN"
REFRESH_KEY = "CLAUDE_CODE_OAUTH_REFRESH_TOKEN"
EXPIRES_KEY = "CLAUDE_CODE_OAUTH_EXPIRES_AT"
REFRESHER = Path.home() / "Data/2-datacore/2-projects/datacore-app/daemon/ops/token-refresh/cos_token_refresh.py"


def _fingerprint(value: str) -> str:
    """Identify a secret without revealing it."""
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()[:12] if value else "(empty)"


def _read_env(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise SystemExit(f"cannot read {path}: {exc}")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def _active_account() -> str:
    """Which account the CLI last signed in as, for display only."""
    try:
        d = json.loads((Path.home() / ".claude.json").read_text())
        return d.get("oauthAccount", {}).get("emailAddress") or "(unknown)"
    except Exception:  # noqa: BLE001
        return "(unknown)"


def status() -> int:
    env = _read_env(ENV_FILE)
    tok = env.get(TOKEN_KEY, "")
    writable = os.access(ENV_FILE, os.W_OK)
    print(f"  store        {ENV_FILE}")
    print(f"  account      {_active_account()}")
    print(f"  token        {_fingerprint(tok)}")
    print(f"  refresh      {'present' if env.get(REFRESH_KEY) else 'ABSENT'}")
    if env.get(EXPIRES_KEY):
        import datetime
        try:
            secs = float(env[EXPIRES_KEY])
            secs = secs / 1000 if secs > 1e12 else secs
            print(f"  expires      {datetime.datetime.fromtimestamp(secs).isoformat()[:19]}")
        except ValueError:
            pass
    # A read-only store is not cosmetic: rotations cannot persist, and because
    # refresh tokens are single-use, each failed write-back destroys the
    # credential rather than renewing it. That is what caused re-auth every few
    # days for weeks.
    print(f"  writable     {'yes' if writable else 'NO — rotations cannot persist, credential will die'}")
    return 0 if writable else 1


def verify() -> tuple[bool, str]:
    """A live call. The only honest evidence that a switch worked."""
    binary = subprocess.run(["which", "claude"], capture_output=True, text=True).stdout.strip()
    if not binary:
        return False, "claude not on PATH"
    env = dict(os.environ)
    env.update(_read_env(ENV_FILE))
    env["DATACORE_HEADLESS"] = "1"
    try:
        r = subprocess.run([binary, "-p", "Reply with exactly: OK", "--output-format", "json"],
                           capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL, env=env)
    except subprocess.TimeoutExpired:
        return False, "timed out"
    raw = r.stdout or ""
    i = raw.find('{"')
    if i < 0:
        return False, (r.stderr or raw)[:120]
    try:
        d = json.loads(raw[i:])
    except ValueError:
        return False, raw[:120]
    if d.get("is_error"):
        return False, str(d.get("result"))[:120]
    return True, f"cost {d.get('total_cost_usd')}"


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9._\-]{24,}")


def extract_token(raw: str) -> str:
    """Pull the token out of whatever `claude setup-token` printed.

    This used to be re.fullmatch over the whole of stdin, which meant the
    pipeline this script's own docstring recommends —

        claude setup-token | claude_account_switch.py set

    — could never work. `setup-token` is an interactive OAuth flow with no
    plain-output mode: it prints a banner, prompts and ANSI colour around the
    token, so stdin is never *only* a token and the switch died on "that does
    not look like a token" with the real token sitting in the input.

    Strip the escape sequences, then take the token-shaped run. Ambiguity is
    refused rather than guessed: writing the wrong string into the fleet's
    credential store is a worse failure than stopping and asking, and it
    would not surface until the next unattended run.
    """
    text = _ANSI.sub("", raw).replace("\r", "\n")

    # Whole-input case (a human pasting, or an already-clean pipe).
    stripped = text.strip()
    if _TOKEN_SHAPE.fullmatch(stripped):
        return stripped

    candidates = _TOKEN_SHAPE.findall(text)
    # Claude's long-lived tokens carry this prefix; when present it is decisive.
    prefixed = [c for c in candidates if c.startswith("sk-ant-")]
    if prefixed:
        unique = sorted(set(prefixed), key=len, reverse=True)
        if len(unique) > 1:
            raise SystemExit(
                f"found {len(unique)} different sk-ant- tokens in the input — "
                "refusing to guess which one to install. Pipe only the token, "
                "or run `set` with no pipe and paste it at the prompt."
            )
        return unique[0]

    if not candidates:
        raise SystemExit(
            "no token found on stdin. `claude setup-token` is interactive and "
            "prints UI around the token — if you piped it and saw nothing "
            "useful, run `set` with no pipe and paste the token at the prompt."
        )

    unique = sorted(set(candidates), key=len, reverse=True)
    if len(unique) > 1 and len(unique[0]) == len(unique[1]):
        raise SystemExit(
            "could not tell which of several strings on stdin is the token — "
            "refusing to guess. Run `set` with no pipe and paste it at the prompt."
        )
    return unique[0]


def set_token(check_only: bool) -> int:
    if check_only:
        ok, detail = verify()
        print(f"  live check   {'OK' if ok else 'FAILED'} — {detail}")
        return 0 if ok else 1

    if sys.stdin.isatty():
        print("  paste the token from `claude setup-token`, then Ctrl-D:", file=sys.stderr)
    raw = sys.stdin.read()
    if not raw.strip():
        raise SystemExit("no token on stdin")
    token = extract_token(raw)

    if not os.access(ENV_FILE, os.W_OK):
        raise SystemExit(f"{ENV_FILE} is not writable by {os.environ.get('USER', 'this user')} — "
                         f"fix the mode first, or rotations will not persist")

    old = _read_env(ENV_FILE).get(TOKEN_KEY, "")
    if token == old:
        print("  token unchanged — nothing to do")
        return 0

    path = Path(ENV_FILE)
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.startswith(TOKEN_KEY + "="):
            lines[i] = f"{TOKEN_KEY}={token}"
            break
    else:
        lines.append(f"{TOKEN_KEY}={token}")
    # A setup-token value is not part of a refresh pair. Leaving the previous
    # account's refresh token in place would let the refresher try to renew
    # THIS token with THAT account's credential — the two are unrelated.
    lines = [l for l in lines if not l.startswith(REFRESH_KEY + "=")
             and not l.startswith(EXPIRES_KEY + "=")]
    path.write_text("\n".join(lines).rstrip("\n") + "\n")
    print(f"  wrote token  {_fingerprint(old)} -> {_fingerprint(token)}")
    print("  cleared      stale refresh/expiry from the previous account")

    if REFRESHER.exists():
        r = subprocess.run([sys.executable, str(REFRESHER)], capture_output=True, text=True)
        for line in (r.stdout or "").splitlines():
            if any(w in line for w in ("propagated", "in sync", "restart", "CANNOT")):
                print(f"  {line.strip()}")
    else:
        print(f"  WARNING: refresher not found at {REFRESHER} — consumers NOT updated")

    ok, detail = verify()
    print(f"  live check   {'OK' if ok else 'FAILED'} — {detail}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("op", choices=["status", "set"])
    ap.add_argument("--check", action="store_true", help="verify only; change nothing")
    a = ap.parse_args()
    return status() if a.op == "status" else set_token(a.check)


if __name__ == "__main__":
    raise SystemExit(main())
