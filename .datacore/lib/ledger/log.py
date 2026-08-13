"""EventLog: locked append, per-writer files, merged read.

Each actor writes to its own file, `<space_dir>/.datacore/events/<actor>.jsonl`
(one canonical-JSON line per event, chained via `prev`/`hash` -- see
`ledger.events`). Only the actor that owns a file ever appends to it, but
multiple *processes* for that same actor (or same-actor workers on one
machine) can race to append concurrently -- `fcntl.flock` around the
read-tail + compute-next + write critical section serializes them so the
chain never forks or gets duplicate/gapped `seq` values (per ENG-2026-0304-027).

`read_events` merges every writer's file for a space and returns them sorted
by `hlc` (the cross-writer serialization order), which is a read-only view --
it does not require or take any lock.

Torn vs. corrupt lines: a process can crash (or another appender can be
mid-flush) leaving a file's FINAL line incomplete/unparseable. That is not
corruption -- it is an in-flight write -- so `append()` truncates it away
(under the lock, restoring the invariant that the file ends with a complete
event) and `read_events()` just skips it silently. A malformed line anywhere
ELSE in the file (i.e. not the last line) cannot be explained by an in-flight
write and is treated as real corruption: both raise `CorruptLogError` naming
the file and the 1-based line number, rather than silently dropping data.

CROSS-ACTOR ORDERING (Task 5.2b): `read_events` sorts by `hlc` string, and a
same-millisecond tie between two DIFFERENT actors' first tick of that
millisecond used to tie-break on the trailing actor name (fixed-width
`pt.counter` prefix ties, `actor` suffix decides) -- letting, say, an
`aaa`-actor's `item.dismiss` sort before a `zzz`-actor's `item.create` it
targets, even though the create happened first in real time. That is a
silent orphan: the dismiss never resurfaces, breaking the never-resurface
guarantee `fold()` relies on.

The fix: `append()` computes its stamp from a `floor` that is the max hlc
across NOT JUST this writer's own tail but every sibling writer's tail too
(read under the same exclusive lock, before stamping). This makes
same-machine cross-actor ordering append-causal -- an append that starts
after another writer's append has already landed on disk is guaranteed to
sort after it, regardless of actor name. It does NOT make ordering
wall-clock-causal across machines: cross-MACHINE ordering (two hosts
appending without either having seen the other's file yet) remains
ownership-partitioning's job, since events only travel between machines via
git sync and there is no global clock to arbitrate a true race between
hosts that never observed each other before stamping.

Floor reads are best-effort: a corrupted or unreadable sibling contributes
nothing and never blocks another actor's append; its corruption surfaces via
read_events/verify_chain, not here.

Residual concurrency caveat: genuinely OVERLAPPING cross-actor appends
(neither writer's flush observed by the other before stamping) can still tie
on (ms, counter) and fall back to actor-name ordering -- the floor guarantees
sequential-observation causality only; true concurrent ties remain and are
accepted (ownership partitioning makes same-item cross-actor same-ms writes
rare by design).
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

from .events import EVENT_TYPES, Event, body_dict, canonical_bytes, compute_hash, from_line, to_line
from .hlc import tick
from .keys import ensure_keypair, sign


class StaleLogError(RuntimeError):
    """Raised when appending would reuse a seq because the log was rewound.

    A hard error rather than a silent renumber: renumbering would hide the
    fact that this machine's log disagrees with the fleet's, which is exactly
    the condition an operator has to know about.
    """


class CorruptLogError(ValueError):
    """A non-final line in an event-log file failed to parse.

    This is real corruption (as opposed to a torn final line, which is
    treated as a crash artifact / in-flight write and handled separately by
    each caller). The message names the offending file and its 1-based line
    number so an operator can locate and repair it -- repair is tooling's
    job, not the reader's, so this is raised loudly rather than skipped.
    """


class EventLog:
    """Append-only event log for one actor within one space.

    Args:
        space_dir: root of the space (events live under
            `<space_dir>/.datacore/events/`).
        actor: this writer's identity -- also the signing key identity and
            the per-writer file name (`<actor>.jsonl`).
        keys_dir: passed through to `ensure_keypair`/`sign` (default
            `~/.datacore/keys`); override in tests to stay in tmp_path.
        registry_path: passed through to `ensure_keypair` (default tracked
            registry.yaml); override in tests to stay in tmp_path.
        sign: tri-state signing switch (Task 1.4b -- signing is opt-in, not
            the MVP default). `True`/`False` picks explicitly; `None`
            (default) falls back to the `DATACORE_LEDGER_SIGN=1` env var.
            When signing resolves to OFF, `ensure_keypair` is NEVER called --
            the default path must not touch key material or create key /
            registry files -- and every appended event gets `sig=""`. When ON,
            behavior is unchanged from before: `ensure_keypair` runs at init
            and every event body is signed.
    """

    def __init__(
        self,
        space_dir: Path,
        actor: str,
        keys_dir: Path | None = None,
        registry_path: Path | None = None,
        sign: bool | None = None,
    ) -> None:
        self.space_dir = Path(space_dir)
        self.actor = actor
        self.keys_dir = keys_dir
        self.registry_path = registry_path
        self.sign = sign if sign is not None else os.environ.get("DATACORE_LEDGER_SIGN") == "1"
        self.path = self.space_dir / ".datacore" / "events" / f"{actor}.jsonl"
        if self.sign:
            # Acceptable to do at init (keeps callers/tests hermetic): idempotent,
            # reuses an existing key rather than regenerating.
            ensure_keypair(actor, keys_dir=keys_dir, registry_path=registry_path)

    def append(self, type: str, payload: dict) -> Event:
        """Append a new event of `type` with `payload`, chained to this
        writer's last event. Raises `ValueError` for an unknown event type.
        """
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {type!r} (expected one of {sorted(EVENT_TYPES)})")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        # "a+b": creates the file if absent, allows both read (for the tail)
        # and append-write. Concurrent O_CREAT opens of the same path are
        # safe on their own; the actual hazard -- two processes reading the
        # same "last event" and computing the same next seq/prev -- is what
        # the flock below prevents.
        with open(self.path, "a+b") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                raw = f.read()
                events, valid_len = _parse_log_bytes(raw, self.path)
                if valid_len < len(raw):
                    # Torn final line from a crash/in-flight write: the write
                    # never completed, so drop it and restore the invariant
                    # that the file ends with a complete event. Because the
                    # fd has O_APPEND set, the next write() below still lands
                    # at the new (truncated) end regardless of seek position.
                    f.truncate(valid_len)
                    f.flush()
                last = events[-1] if events else None

                # HIGH-WATER MARK: refuse to append against a REWOUND log.
                #
                # seq comes from this file's own tail, which is only safe while
                # the file is current. A git operation that rewinds it — a
                # merge taking "theirs", a checkout, a bad conflict resolution
                # — silently lowers that tail, and the next append then REUSES
                # a seq that already identifies a different event on another
                # machine. Two events, one (actor, seq): the fork this whole
                # design exists to make impossible.
                #
                # That is not hypothetical. It happened in 5-plur on
                # 2026-08-13: seq 139 was an item.update on one machine and an
                # item.dismiss on another, found only because git happened to
                # produce a text conflict. Had the two sides merged cleanly the
                # fork would have been silent and permanent.
                #
                # The mark lives OUTSIDE the log, in state/, so rewinding the
                # tracked file cannot rewind the memory of how far it got.
                # State is machine-local and disposable by design — losing it
                # only costs the guard, never data.
                hwm_path = (self.path.parent.parent / "state" / "seq-hwm"
                            / f"{self.actor}.seq")
                hwm = -1
                try:
                    hwm = int(hwm_path.read_text().strip())
                except (OSError, ValueError):
                    pass
                tail_seq = last.seq if last is not None else -1
                if tail_seq < hwm:
                    raise StaleLogError(
                        f"{self.path.name} ends at seq {tail_seq} but this "
                        f"machine already wrote seq {hwm}. The log was rewound "
                        f"(bad merge/checkout). Appending now would reuse a "
                        f"seq and fork the log. Converge first, then retry."
                    )

                if last is None:
                    seq, prev = 0, "GENESIS"
                else:
                    seq = last.seq + 1
                    prev = last.hash

                # Cross-actor causal floor (Task 5.2b, see module docstring
                # CROSS-ACTOR ORDERING): the stamp must not regress behind
                # ANY writer's last event, not just this actor's own -- so
                # fold over own-tail hlc and every sibling file's tail hlc
                # (still under this same flock) and tick from the max.
                # Fixed-width `pt.counter` prefix means plain string max is
                # correct here (only the trailing actor name can differ
                # among ties, and tick()/parse() below ignore that field).
                floor = last.hlc if last is not None else None
                for sibling_path in self.path.parent.glob("*.jsonl"):
                    if sibling_path.name == self.path.name:
                        continue
                    # NOTE: reads each sibling's ENTIRE file just to get its
                    # tail -- acceptable while files are small; a tail-seek
                    # optimization is future work, out of scope here.
                    #
                    # Best-effort: the floor is a nice-to-have ordering
                    # improvement, not a correctness requirement of THIS
                    # actor's own chain -- a sibling that is corrupt
                    # (CorruptLogError, a non-tail malformed line) or
                    # unreadable (OSError, e.g. permissions, races with
                    # deletion) must never block this append. It simply
                    # contributes nothing to the floor; its corruption still
                    # surfaces loudly via read_events/verify_chain, which is
                    # where diagnosis belongs.
                    try:
                        sibling_raw = sibling_path.read_bytes()
                        sibling_events, _ = _parse_log_bytes(sibling_raw, sibling_path)
                    except (CorruptLogError, OSError):
                        continue
                    if sibling_events:
                        sibling_last_hlc = sibling_events[-1].hlc
                        if floor is None or sibling_last_hlc > floor:
                            floor = sibling_last_hlc
                hlc_stamp = tick(self.actor, floor)

                body = body_dict(seq, hlc_stamp, self.actor, type, payload, prev)
                # Signing contract: sign the BODY, never the serialized line.
                # Opt-in (Task 1.4b): unsigned events get sig="" and never
                # touch key material.
                sig = sign(self.actor, canonical_bytes(body), keys_dir=self.keys_dir) if self.sign else ""
                event_hash = compute_hash(body)
                event = Event(**body, hash=event_hash, sig=sig)

                f.write((to_line(event) + "\n").encode("utf-8"))
                f.flush()
                # Record the mark only AFTER the event is on disk, so a crash
                # between the two leaves the guard permissive rather than
                # blocking a legitimate retry. Failure to write it is never
                # fatal: the guard is a safety net, and losing the net must not
                # stop the work.
                try:
                    hwm_path.parent.mkdir(parents=True, exist_ok=True)
                    hwm_path.write_text(str(seq))
                except OSError:
                    pass
                return event
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def _parse_log_bytes(raw: bytes, path: Path) -> tuple[list[Event], int]:
    """Parse one writer file's raw bytes into (valid_events, valid_byte_length).

    `valid_byte_length` is how many leading bytes of `raw` make up complete,
    parseable lines -- i.e. the safe truncation point if the tail is torn.
    A well-formed file (every line parses) has `valid_byte_length == len(raw)`.

    Only the LAST line, if unparseable, is treated as a torn/in-flight write
    and excluded (not raised); it is simply absent from `valid_events` and
    excluded from the byte count. An unparseable line anywhere else raises
    `CorruptLogError` naming `path` and its 1-based line number -- that
    can't be explained by a write-in-progress and is real corruption.
    """
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        # Trailing empty element from the final "\n" of a complete write.
        lines.pop()

    events: list[Event] = []
    valid_len = 0
    n = len(lines)
    for i, raw_line in enumerate(lines):
        is_last = i == n - 1
        line = raw_line.strip()
        if not line:
            if not is_last:
                valid_len += len(raw_line) + 1  # + the "\n" split on
            continue
        try:
            event = from_line(line.decode("utf-8"))
        except Exception as exc:
            if is_last:
                break  # torn tail -- valid_len already excludes it
            raise CorruptLogError(
                f"corrupt event log {path}: malformed line {i + 1}: {exc}"
            ) from exc
        events.append(event)
        valid_len += len(raw_line) + 1

    return events, valid_len


def read_events(space_dir: Path) -> list[Event]:
    """Merge every writer's `*.jsonl` file under `space_dir` into one list,
    sorted by `hlc` (ascending). Returns `[]` if the events dir is missing
    or empty. Read-only: takes no lock, never mutates a file.

    A torn final line (a live writer mid-flush, or a crash) is skipped
    silently. A malformed line anywhere else raises `CorruptLogError`.
    """
    events_dir = Path(space_dir) / ".datacore" / "events"
    events: list[Event] = []
    if not events_dir.exists():
        return events
    for path in sorted(events_dir.glob("*.jsonl")):
        raw = path.read_bytes()
        file_events, _valid_len = _parse_log_bytes(raw, path)
        events.extend(file_events)
    events.sort(key=lambda e: e.hlc)
    return events
