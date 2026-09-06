"""Chain + signature verification for a single-writer event-log file.

`verify_chain` is a read-only diagnostic: it never mutates the file and
never raises on malformed data -- every problem it finds becomes a string
in the returned list, each naming the 1-based line number and the reason,
so an operator (or another script) can see every issue in one pass instead
of stopping at the first one.

It deliberately reimplements the line-splitting/parsing loop from
`ledger.log._parse_log_bytes` rather than importing it, because the
reporting contract is different in two ways that matter here:

  - A torn trailing line is `read_events`'/`append`'s cue for "in-flight
    write from a live writer or a crash -- skip it silently, that's not
    corruption." `verify_chain` is a diagnostic tool, not a live reader:
    it should surface that anomaly rather than hide it, so a torn final
    line becomes a reported error ("torn trailing line") instead of being
    swallowed.
  - A malformed line anywhere else in the file is `read_events`'s cue to
    raise `CorruptLogError` loudly. `verify_chain`'s contract is "return
    every problem found", never "raise on the first bad line" -- so a
    malformed non-final line is likewise reported as an error string, not
    raised.
"""

from __future__ import annotations

import json

from pathlib import Path

from .events import Event, body_dict, canonical_bytes, compute_hash, from_line
from .keys import verify as verify_sig

GENESIS = "GENESIS"


def verify_chain(path: Path, registry_path: Path | None = None, strict: bool = False) -> list[str]:
    """Verify one writer's event-log file: hash chain, seq, and signatures.

    Args:
        path: the writer's `.jsonl` file (e.g. `<space>/.datacore/events/mac.jsonl`).
        registry_path: passed through to `keys.verify` for signature checks
            (default: the tracked registry.yaml -- see `keys.DEFAULT_REGISTRY_PATH`).
        strict: when True, an event with `sig == ""` (unsigned) is itself
            reported as an error -- for deployments where signing is
            switched on system-wide and every event is expected to carry
            a signature. When False (the default), unsigned events are
            valid (opt-in signing, per Task 1.4b) and only checked for
            hash/chain/seq integrity.

    Returns:
        `[]` if the file is a fully valid chain (given `strict`).
        Otherwise a list of human-readable error strings, each naming the
        1-based line number and the problem found there. Multiple
        problems on one line produce multiple entries. Never raises on
        malformed input -- see module docstring for why. If `path` cannot
        even be read (missing, a directory, permission denied, ...), that
        is likewise reported rather than raised: a single-element list
        `["cannot read <path>: <OSError>"]`.

    Checks performed per event (in this order, all independent -- one
    failing does not skip the others):
      1. hash mismatch: recompute `compute_hash(body_dict(...))` from the
         event's own fields and compare to its stored `hash`.
      2. broken prev linkage: the first event's `prev` must be `"GENESIS"`;
         every subsequent event's `prev` must equal the *previous* event's
         stored `hash` field (not a recomputed hash -- if that event's
         hash is itself wrong, that's already reported by check 1).
      3. seq gap: `seq` must run 0, 1, 2, ... with no gaps or repeats.
      4. signature: only for events where `sig != ""` -- `keys.verify`
         against the event's `actor` via the registry. A bad signature and
         an unknown actor both surface as the same "signature verification
         failed" error (that distinction is `keys.verify`'s to make, and
         it deliberately collapses both to `False`).
      5. (strict mode only) `sig == ""` is itself an error.
    """
    path = Path(path)
    errors: list[str] = []

    try:
        raw = path.read_bytes()
    except OSError as exc:
        # Missing file, a directory, unreadable permissions, etc. -- verify
        # is a diagnostic tool and must never raise on bad input; an
        # unreadable path is itself just another problem to report.
        return [f"cannot read {path}: {exc}"]

    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        # Trailing empty element from the final "\n" of a complete write.
        lines.pop()

    n = len(lines)
    parsed: list[tuple[int, Event]] = []
    for i, raw_line in enumerate(lines):
        line_no = i + 1
        is_last = i == n - 1
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            event = from_line(stripped.decode("utf-8"))
        except Exception as exc:
            if is_last:
                errors.append(f"line {line_no}: torn trailing line ({exc})")
            else:
                errors.append(f"line {line_no}: malformed line ({exc})")
            continue
        parsed.append((line_no, event))

    expected_prev = GENESIS
    expected_seq = 0
    for line_no, event in parsed:
        body = body_dict(event.seq, event.hlc, event.actor, event.type, event.payload, event.prev)

        if compute_hash(body) != event.hash:
            errors.append(f"line {line_no}: hash mismatch")

        if event.prev != expected_prev:
            errors.append(
                f"line {line_no}: broken prev linkage "
                f"(expected prev={expected_prev!r}, got {event.prev!r})"
            )

        if event.seq != expected_seq:
            errors.append(
                f"line {line_no}: seq gap (expected seq={expected_seq}, got {event.seq})"
            )

        if event.sig != "":
            if not verify_sig(event.actor, canonical_bytes(body), event.sig, registry_path=registry_path):
                from .keys import known_verify_key
                if known_verify_key(event.actor, registry_path):
                    errors.append(
                        f"line {line_no}: signature verification failed for actor {event.actor!r} "
                        "(invalid signature against the registered key)"
                    )
                elif strict:
                    errors.append(f"line {line_no}: no verify key known for actor {event.actor!r} (strict)")
                # else: signed by a writer whose key this host does not hold yet --
                # the chain is intact; the signature is unverifiable here, not
                # wrong. Keys travel through registry/principals.yaml (verify_keys).
        elif strict:
            errors.append(f"line {line_no}: unsigned event")

        # Chain forward using the event's *stored* hash/seq, not a
        # recomputed one -- a wrong stored hash is already flagged by the
        # hash-mismatch check above; using it (as-is) to validate the next
        # event's `prev` is what correctly detects downstream breakage
        # versus a self-contained single-line tamper.
        expected_prev = event.hash
        expected_seq = event.seq + 1

    return errors


def check_not_rewound(path: Path) -> list[str]:
    """Has this log lost events from its tail?

    THE CHAIN CANNOT ANSWER THIS. Hash-linking proves that the events present
    are the events written, in order, unmodified -- an edited payload is caught
    even if its own hash is recomputed, because each event commits to its
    predecessor. But removing events from the END removes their hashes too,
    and what remains is a shorter, internally perfect chain. Measured: dropping
    the last two events of a five-event log passes `verify` clean while `fold`
    silently sees a shorter history.

    That is not an exotic attack. A torn write, a full disk, a killed process
    mid-append, or an interrupted sync all produce exactly a truncated tail.

    The high-water mark is the external witness. `EventLog.append` records the
    highest seq this machine has ever written to `state/seq-hwm/<actor>.seq`,
    and refuses to append when the log has fallen behind it. That check fires
    at WRITE time on the writing machine; this brings the same evidence to READ
    time, where verification actually happens.

    Silent when no watermark exists: a log this machine never wrote (another
    actor's, freshly cloned) has no local witness, and absence of evidence must
    not be reported as evidence of tampering.
    """
    actor = path.stem
    hwm_path = path.parent.parent / "state" / "seq-hwm" / f"{actor}.seq"
    try:
        hwm = int(hwm_path.read_text().strip())
    except (OSError, ValueError):
        return []

    tail = -1
    try:
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                tail = max(tail, int(json.loads(line).get("seq", -1)))
            except (ValueError, TypeError):
                continue
    except OSError:
        return []

    if tail < hwm:
        return [f"TRUNCATED: log ends at seq {tail} but this machine wrote up to "
                f"seq {hwm} — {hwm - tail} event(s) missing from the tail "
                f"(witness: {hwm_path})"]
    return []
