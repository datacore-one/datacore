#!/usr/bin/env python3
"""Write-side ledger gate: only the ledger writer adds to the history (LED-3).

WHY. On 2026-09-25 an agent appended two events to 6-meridian's miles.jsonl by
hand -- `json.dumps` with spaces, sig copied from hash, HLCs typed 1000 ms
apart -- then committed and pushed them. Nothing on the write path looked at
the bytes: pre-commit had no ledger check and pre-push only checked WHICH log a
host writes. Verification ran after publication, where an append-only log can
no longer be corrected (ledger audit A#6, D1).

`EventLog.append` always writes `to_line(event)`, the canonical bytes, so a
line that is not byte-identical to its own canonical encoding was not written
by the library. That one check catches every integrity incident the fleet has
had. The rest make the same point for impostors and edits.

WHAT IT CHECKS, for every staged (`--staged`) or pushed (`--ranges A..B ...`)
change to `.datacore/events/*.jsonl`:
  * append-only: every existing line is unchanged and nothing is deleted;
  * each new line is canonical bytes, exactly as EventLog writes them;
  * `actor` is the log's writer (the file stem, run-branch suffix removed);
  * chain continuity: seq is the previous seq + 1, prev is the previous hash;
  * `hash` is the hash of the body;
  * the HLC is not more than 10 minutes ahead of this machine's clock.

Signatures are not checked: signing is not required yet (owner decision
2026-09-26); it arrives with FDS-ID.

Exit 0 = clean, 1 = refused (each problem named on stderr), 2 = cannot run.
Stdlib only, Python 3.9+: pre-commit may run under a system python3.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ledger.events import body_dict, canonical_bytes, compute_hash  # noqa: E402

FUTURE_TOLERANCE_MS = 10 * 60 * 1000
PATHSPEC = ".datacore/events/*.jsonl"
FIELDS = {"seq", "hlc", "actor", "type", "payload", "prev", "hash", "sig"}
ZERO = "0" * 40
_RUN_SUFFIX = re.compile(r"-run-\d{4}-\d{2}-\d{2}$")


def writer_of(path: str) -> str:
    stem = Path(path).name[:-len(".jsonl")]
    return _RUN_SUFFIX.sub("", stem.strip().lower())


def _last_event(old: bytes):
    """(seq, hash) of the last parseable line of the prior content, or None."""
    for line in reversed(old.split(b"\n")):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            return d.get("seq"), d.get("hash")
        except ValueError:
            continue
    return None


def check_change(path: str, old: bytes, new: bytes | None, now_ms: int | None = None) -> list[str]:
    """Every problem with replacing `old` by `new` for one log file."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if new is None:
        return [f"{path}: log deleted (the ledger is append-only)"] if old else []
    prefix = old
    if old and not old.endswith(b"\n"):
        # A torn (unterminated) final line may be truncated by the next genuine
        # append; everything before it must still be intact.
        tail = old[old.rfind(b"\n") + 1:]
        try:
            json.loads(tail)
        except ValueError:
            prefix = old[:old.rfind(b"\n") + 1]
        if new.startswith(old) and new[len(old):len(old) + 1] == b"\n":
            prefix = old + b"\n"
    if not new.startswith(prefix):
        old_lines, new_lines = prefix.split(b"\n"), new.split(b"\n")
        n = next((i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b),
                 min(len(old_lines), len(new_lines)))
        return [f"{path} line {n + 1}: existing history changed or removed (the ledger is append-only)"]

    errors: list[str] = []
    last = _last_event(prefix)
    prev_seq, prev_hash = (last if last else (-1, "GENESIS"))
    writer = writer_of(path)
    base_line = prefix.count(b"\n")
    added = new[len(prefix):]
    pieces = added.split(b"\n")
    if pieces and pieces[-1] == b"":
        pieces.pop()
    elif pieces:
        errors.append(f"{path} line {base_line + len(pieces)}: unterminated line")
    for i, raw in enumerate(pieces):
        n = base_line + i + 1
        if not raw.strip():
            errors.append(f"{path} line {n}: blank line")
            continue
        try:
            d = json.loads(raw)
        except ValueError as exc:
            errors.append(f"{path} line {n}: not JSON ({exc})")
            continue
        if not isinstance(d, dict) or set(d) != FIELDS:
            errors.append(f"{path} line {n}: not an event envelope (fields {sorted(d) if isinstance(d, dict) else type(d).__name__})")
            continue
        if raw != canonical_bytes(d):
            errors.append(f"{path} line {n}: not canonical bytes -- not written by EventLog "
                          "(hand-written lines are refused; append with ledger_cli.py or EventLog)")
        if d["actor"] != writer:
            errors.append(f"{path} line {n}: actor {d['actor']!r} is not this log's writer {writer!r}")
        if d["seq"] != (prev_seq + 1 if isinstance(prev_seq, int) else None) or type(d["seq"]) is not int:
            errors.append(f"{path} line {n}: seq {d['seq']!r} does not follow {prev_seq!r}")
        if d["prev"] != prev_hash:
            errors.append(f"{path} line {n}: prev does not link to the previous event's hash")
        try:
            body = body_dict(d["seq"], d["hlc"], d["actor"], d["type"], d["payload"], d["prev"])
            if compute_hash(body) != d["hash"]:
                errors.append(f"{path} line {n}: hash does not match the event body")
        except (TypeError, ValueError) as exc:
            errors.append(f"{path} line {n}: body cannot be hashed ({exc})")
        try:
            physical = int(str(d["hlc"]).split(".")[0])
            if physical > now_ms + FUTURE_TOLERANCE_MS:
                errors.append(f"{path} line {n}: HLC is {(physical - now_ms) // 60000} min in the future "
                              f"(tolerance {FUTURE_TOLERANCE_MS // 60000} min)")
        except ValueError:
            errors.append(f"{path} line {n}: HLC {d['hlc']!r} is malformed")
        prev_seq, prev_hash = d["seq"], d["hash"]
    return errors


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True)


def _blob(repo: Path, spec: str) -> bytes | None:
    r = _git(repo, "show", spec)
    return r.stdout if r.returncode == 0 else None


def _changed(repo: Path, *diff_args: str) -> list[str]:
    r = _git(repo, "diff", "--no-renames", "--name-only", "-z", *diff_args, "--", PATHSPEC)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace").strip())
    return [p for p in r.stdout.decode().split("\0") if p.endswith(".jsonl") and "/.datacore/events/" in f"/{p}"]


def staged(repo: Path) -> list[str]:
    has_head = _git(repo, "rev-parse", "--verify", "-q", "HEAD").returncode == 0
    errors = []
    for p in _changed(repo, "--cached"):
        old = (_blob(repo, f"HEAD:{p}") or b"") if has_head else b""
        errors += check_change(p, old, _blob(repo, f":{p}"))
    return errors


def ranges(repo: Path, specs: list[str]) -> list[str]:
    errors = []
    for spec in specs:
        if ".." not in spec:
            # The pre-push hook degrades to a bare tip when it cannot find a
            # base; checking all history then would refuse every legacy line
            # forever. Say so rather than pretend it was checked.
            print(f"ledger-write-gate: no base for {spec[:12]}; its ledger lines were not checked "
                  "(fetch and retry)", file=sys.stderr)
            continue
        base, tip = spec.split("..", 1)
        if not base or base == ZERO:
            print(f"ledger-write-gate: no base for {spec}; not checked", file=sys.stderr)
            continue
        for p in _changed(repo, base, tip):
            errors += check_change(p, _blob(repo, f"{base}:{p}") or b"", _blob(repo, f"{tip}:{p}"))
    return errors


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="check the index against HEAD (pre-commit)")
    mode.add_argument("--ranges", nargs="*", help="check base..tip ranges (pre-push)")
    args = ap.parse_args(argv)
    repo = Path(args.repo)
    try:
        errors = staged(repo) if args.staged else ranges(repo, args.ranges or [])
    except (OSError, RuntimeError) as exc:
        print(f"ledger-write-gate: cannot run: {exc}", file=sys.stderr)
        return 2
    if not errors:
        return 0
    print("ledger-write-gate: REFUSED -- the event ledger only accepts lines written by EventLog:",
          file=sys.stderr)
    for e in errors:
        print(f"  {e}", file=sys.stderr)
    print("A bad event already published is cancelled by an authorised in-ledger void "
          "(ledger_cli.py void), never by editing the log.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
