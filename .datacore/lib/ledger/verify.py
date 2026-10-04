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

ONE VERIFIER (ledger upgrade Phase 2, V2; audit A#7). Every consumer that asks
"is this ledger sound?" -- the CLI, the health check, the relay guard, the seal,
the checkpoint restore -- asks this module, and gets the same answer:

  * `check_events` judges one chain EVENT BY EVENT (decision 7): each problem
    names its line, and the events before it are not condemned.
  * A problem is `certain` (a fault in the data) or not: a writer whose verify
    key this machine does not hold cannot have its signatures judged HERE.
    That is "could not tell" on this machine, never "broken" (V5) -- except in
    strict mode, where an unverifiable signature is itself an error.
  * `verify_log` / `verify_space` return reports with a verdict of
    ok | broken | unknown; `chain_problems` does the same for an in-memory
    event list (seal, checkpoint, prefix restore).
  * INCREMENTAL (A#8): after a clean verify, a machine-local marker records how
    far the log was verified (byte length, sha256 of those bytes, last seq and
    hash, and a digest of everything the verdict depended on: verifier version,
    keys, principals, voids). The next verify checks the prefix still hashes to
    the same digest and judges only what was appended. `--full` (and the daily
    job) re-verifies everything.

`verify_chain` / `verify_events` keep their list-of-strings contract: every
problem, certain or not, is returned, so the callers that fail closed on any
problem (job attestations, claims) still do.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

from dataclasses import dataclass, field
from pathlib import Path

from .events import Event, body_dict, canonical_bytes, compute_hash, from_line
from .keys import verify as verify_sig

GENESIS = "GENESIS"
#: Bumped whenever a rule changes what a clean verdict means: every marker
#: written by an older verifier is then ignored and the log re-verified in full.
VERIFIER_VERSION = 2


@dataclass(frozen=True)
class Problem:
    text: str                      # "line N: ..." -- the words every caller prints
    line: int | None = None
    certain: bool = True           # False: could not be judged on this machine
    #: "clock" for a wrong or malformed hlc: flagged by verify (LED-7), never
    #: followed by the fold, and never a reason to stop syncing (see chain_problems)
    kind: str = "chain"


@dataclass
class LogReport:
    path: Path
    shown: str
    events: int = 0
    problems: list = field(default_factory=list)
    #: lines taken as already verified from this machine's marker (0 = full verify)
    trusted_lines: int = 0

    @property
    def verdict(self) -> str:
        return verdict_of(self.problems)


@dataclass
class SpaceReport:
    space: Path
    logs: list = field(default_factory=list)

    @property
    def problems(self) -> list:
        return [p for log in self.logs for p in log.problems]

    @property
    def verdict(self) -> str:
        return verdict_of(self.problems)

    @property
    def events(self) -> int:
        return sum(log.events for log in self.logs)


def verdict_of(problems) -> str:
    """ok | broken | unknown: one certain problem is broken; only uncertain ones, unknown."""
    if any(p.certain for p in problems):
        return "broken"
    return "unknown" if problems else "ok"


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
    return [p.text for p in verify_log(path, registry_path=registry_path, strict=strict).problems]


def verify_events(parsed: list[tuple[int, Event]], registry_path: Path | None = None,
                  strict: bool = False,
                  voids=None, log: str | None = None) -> list[str]:
    """Verify one already-read chain without rereading a mutable source file.

    Callers preserve chain order and supply record numbers. Every problem,
    certain or not, is returned (fail closed); see `check_events` for the
    classified form.
    """
    return [p.text for p in check_events(parsed, registry_path=registry_path, strict=strict,
                                         voids=voids, log=log)]


#: (actor, sig, body hash, at_ms, key-file signatures) -> verified. A signature
#: is judged once per process: the checkpoint restores the same chains it just
#: saved, and the seal verifies the events the space verify already judged.
_SIG_MEMO: dict = {}


def _key_context(registry_path: Path | None) -> tuple:
    from . import keys
    reg = Path(registry_path or keys.DEFAULT_REGISTRY_PATH)
    return (keys._stat_key(keys.principals_path()), keys._stat_key(reg), str(reg))


def _signature_ok(event: Event, body: dict, computed: str, registry_path, at_ms, context) -> bool:
    key = (event.actor, event.sig, computed, at_ms, context)
    hit = _SIG_MEMO.get(key)
    if hit is None:
        hit = verify_sig(event.actor, canonical_bytes(body), event.sig,
                         registry_path=registry_path, at_ms=at_ms)
        if len(_SIG_MEMO) > 500_000:
            _SIG_MEMO.clear()
        _SIG_MEMO[key] = hit
    return hit


def _well_formed_sig(sig: str) -> bool:
    """64 bytes of hex: the shape of every Ed25519 signature EventLog writes."""
    if len(sig) != 128:
        return False
    try:
        bytes.fromhex(sig)
        return True
    except ValueError:
        return False


def check_events(parsed: list[tuple[int, Event]], registry_path: Path | None = None,
                 strict: bool = False, voids=None, log: str | None = None,
                 expected_prev: str = GENESIS, expected_seq: int = 0) -> list[Problem]:
    """Judge one chain event by event; every problem names its line.

    `voids` (a `ledger.voids.Voids` for the chain's space) and `log` (this
    chain's file stem): an event cancelled by an effective in-ledger void is
    accepted despite a failed hash or signature check -- its chain position
    (prev, seq) is still checked -- and a `ledger.void` in this chain that has
    no effect is reported.

    `expected_prev` / `expected_seq`: where the chain resumes (an incremental
    verify starts after the last event a marker vouches for).
    """
    from .keys import known_verify_key
    problems: list[Problem] = []
    # A wrong clock is flagged, never followed (LED-7): the same tolerance the
    # writer uses to refuse such a stamp as its causal floor.
    from .log import FUTURE_TOLERANCE_MS
    horizon = int(time.time() * 1000) + FUTURE_TOLERANCE_MS
    context = _key_context(registry_path)
    no_key: dict = {}
    for line_no, event in parsed:
        def bad(text: str, certain: bool = True, kind: str = "chain") -> None:
            problems.append(Problem(f"line {line_no}: {text}", line_no, certain, kind))

        if (type(event.seq) is not int or event.seq < 0
                or not all(isinstance(v, str) for v in (event.hlc, event.actor, event.type, event.prev, event.hash, event.sig))
                or not isinstance(event.payload, dict)):
            bad("invalid event field types")
            continue
        body = body_dict(event.seq, event.hlc, event.actor, event.type, event.payload, event.prev)

        physical = None
        try:
            physical = int(event.hlc.split(".", 1)[0])
            if physical > horizon:
                bad(f"hlc {event.hlc!r} is "
                    f"{(physical - horizon) // 60000 + FUTURE_TOLERANCE_MS // 60000} min "
                    "in the future (wrong clock)", kind="clock")
        except ValueError:
            bad(f"malformed hlc {event.hlc!r}", kind="clock")

        computed = compute_hash(body)
        voided = bool(voids is not None and log is not None and voids.applies(log, event, computed))
        if voids is not None and log is not None and event.type == "ledger.void":
            why = voids.refusal_for(log, event.seq)
            if why:
                bad(why)
        if computed != event.hash and not voided:
            bad("hash mismatch")

        if event.prev != expected_prev:
            bad(f"broken prev linkage (expected prev={expected_prev!r}, got {event.prev!r})")

        if event.seq != expected_seq:
            bad(f"seq gap (expected seq={expected_seq}, got {event.seq})")

        if event.sig != "":
            if not voided and not _signature_ok(event, body, computed, registry_path, physical, context):
                if event.actor not in no_key:
                    no_key[event.actor] = not known_verify_key(event.actor, registry_path)
                # No verify key for this writer HERE: this machine cannot judge
                # a well-formed signature (V5). A "signature" that is not even
                # an Ed25519 signature (the 2026-09-25 forgery wrote its own
                # hash there) is judged without any key: a fault in the data.
                # Strict mode demands every signature be judged.
                bad(_signature_problem(event.actor, registry_path, physical),
                    certain=strict or not no_key[event.actor] or not _well_formed_sig(event.sig))
        elif strict:
            bad("unsigned event")

        damaged = getattr(event, "damaged_after", None)
        if damaged:
            bad(f"chain is incomplete: {damaged}")

        # Chain forward using the event's *stored* hash/seq, not a
        # recomputed one -- a wrong stored hash is already flagged by the
        # hash-mismatch check above; using it (as-is) to validate the next
        # event's `prev` is what correctly detects downstream breakage
        # versus a self-contained single-line tamper.
        expected_prev = event.hash
        expected_seq = event.seq + 1

    return problems


# ── one log ─────────────────────────────────────────────────────────────────

def _state_dir() -> Path:
    return Path(os.environ.get("DATACORE_STATE") or Path.home() / ".datacore" / "state")


def _marker_path(path: Path) -> Path:
    digest = hashlib.sha256(str(Path(path).absolute()).encode()).hexdigest()[:32]
    return _state_dir() / "ledger-verified" / f"{digest}.json"


def _file_digest(path: Path | None) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else ""
    except OSError:
        return "unreadable"


def _context_digest(registry_path, strict: bool, voids) -> str:
    """Everything a clean verdict depended on besides the log's own bytes."""
    from . import keys
    effective = sorted((repr(k), repr(v)) for k, v in getattr(voids, "effective", {}).items())
    refused = sorted((repr(k), repr(v)) for k, v in getattr(voids, "refused", {}).items())
    parts = [str(VERIFIER_VERSION), str(bool(strict)),
             _file_digest(keys.principals_path()),
             _file_digest(Path(registry_path or keys.DEFAULT_REGISTRY_PATH)),
             json.dumps([effective, refused])]
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def _read_marker(path: Path, raw: bytes, context: str) -> dict | None:
    try:
        m = json.loads(_marker_path(path).read_text())
        size, lines = int(m["size"]), int(m["lines"])
        if (m.get("version") != VERIFIER_VERSION or m.get("path") != str(Path(path).absolute())
                or m.get("context") != context or not (0 < size <= len(raw)) or lines < 1
                or raw[size - 1:size] != b"\n"
                or hashlib.sha256(raw[:size]).hexdigest() != m["prefix_sha256"]):
            return None
        int(m["last_seq"]), str(m["last_hash"])
        return m
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_marker(path: Path, raw: bytes, lines: int, last: Event, context: str) -> None:
    """Record a clean verify. Machine-local and disposable: losing it costs one full verify."""
    size = raw.rfind(b"\n") + 1
    if size <= 0 or lines < 1:
        return
    target = _marker_path(path)
    try:
        # Private (0700), like every runtime state folder: a 0755 state folder
        # made by the first verify on a fresh machine is refused by
        # file_utils.private_state_directory, so every later converge failed.
        _state_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
        target.parent.mkdir(mode=0o700, exist_ok=True)
        tmp = target.with_name(f"{target.stem}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({
            "version": VERIFIER_VERSION, "path": str(Path(path).absolute()), "size": size,
            "lines": lines, "prefix_sha256": hashlib.sha256(raw[:size]).hexdigest(),
            "last_seq": last.seq, "last_hash": last.hash, "context": context}))
        tmp.rename(target)
    except OSError:
        pass                      # an unwritable state dir only costs the next run a full verify


def verify_log(path: Path, registry_path: Path | None = None, strict: bool = False,
               incremental: bool = False, shown: str | None = None,
               record: bool | None = None) -> LogReport:
    """Verify one writer's log file: the classified form of `verify_chain`.

    With `incremental`, a marker left by this machine's last clean verify lets
    the verified prefix be confirmed by its digest instead of re-judged event by
    event; only the lines appended since are judged. Any change to the prefix,
    the keys, the principals, the space's voids or the verifier invalidates it.
    `record` (default: `incremental`) leaves that marker after a clean verify,
    so a full verify on a schedule seeds the fast path for everything else.
    """
    path = Path(path)
    record = incremental if record is None else record
    report = LogReport(path=path, shown=shown or path.name)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        # Missing file, a directory, unreadable permissions, etc. -- verify
        # is a diagnostic tool and must never raise on bad input. A log that
        # cannot be read has not been judged: could not tell (a log this
        # machine wrote and that is gone is reported by check_not_rewound).
        report.problems.append(Problem(f"cannot read {path}: {exc}", None, certain=False))
        return report

    # In-ledger voids (ledger.voids): any log of the same space may hold the
    # authorised `ledger.void` that cancels an event of this one. Nothing else
    # excuses a failed check: the out-of-band exception list is retired.
    from .voids import for_events_dir
    voids = for_events_dir(path.parent)
    context = _context_digest(registry_path, strict, voids) if (incremental or record) else ""
    marker = _read_marker(path, raw, context) if incremental else None

    offset, first_line, prev, seq = 0, 0, GENESIS, 0
    if marker:
        offset, first_line = int(marker["size"]), int(marker["lines"])
        prev, seq = str(marker["last_hash"]), int(marker["last_seq"]) + 1
        report.trusted_lines = first_line

    lines = raw[offset:].split(b"\n")
    if lines and lines[-1] == b"":
        # Trailing empty element from the final "\n" of a complete write.
        lines.pop()
    n = len(lines)
    parsed: list[tuple[int, Event]] = []
    problems: list[Problem] = []
    for i, raw_line in enumerate(lines):
        line_no = first_line + i + 1
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            event = from_line(stripped.decode("utf-8"))
        except Exception as exc:
            kind = "torn trailing line" if i == n - 1 else "malformed line"
            problems.append(Problem(f"line {line_no}: {kind} ({exc})", line_no))
            continue
        parsed.append((line_no, event))

    problems += check_events(parsed, registry_path=registry_path, strict=strict, voids=voids,
                             log=path.stem, expected_prev=prev, expected_seq=seq)
    report.problems = problems
    report.events = (int(marker["last_seq"]) + 1 if marker else 0) + len(parsed)
    if record and not problems and parsed:
        _write_marker(path, raw, first_line + n, parsed[-1][1], context)
    return report


def space_logs(space: Path) -> list[tuple[Path, str]]:
    """Every log of a space, as (path, how it is shown): task logs, the per-space
    telemetry logs (decision 3: still history), and any log this machine wrote
    that is now gone (its witness names it; LED-2)."""
    from .log import TELEMETRY_DIR, witness_path
    space = Path(space)
    out: list[tuple[Path, str]] = []
    for folder, prefix in ((space / ".datacore" / "events", ""),
                           (space / ".datacore" / TELEMETRY_DIR, f"{TELEMETRY_DIR}/")):
        held = sorted(folder.glob("*.jsonl")) if folder.is_dir() else []
        out += [(p, prefix + p.name) for p in held]
        stems = {p.stem for p in held}
        marks = witness_path(folder / "x.jsonl").parent
        if marks.is_dir():
            out += [(folder / f"{w.stem}.jsonl", f"{prefix}{w.stem}.jsonl")
                    for w in sorted(marks.glob("*.seq")) if w.stem not in stems]
    return out


def _rewind_problems(path: Path) -> list[Problem]:
    out = []
    for text in check_not_rewound(path):
        # TRUNCATED / REWRITTEN / MISSING are facts about the data; a witness
        # that cannot be read only means it could not be judged.
        certain = text.startswith(("TRUNCATED:", "REWRITTEN:", "MISSING:"))
        out.append(Problem(text, None, certain))
    return out


def verify_space(space: Path, registry_path: Path | None = None, strict: bool = False,
                 incremental: bool = True, witnesses: bool = True) -> SpaceReport:
    """Verify every log of a space -- the one verdict every consumer reports."""
    report = SpaceReport(space=Path(space))
    for path, shown in space_logs(space):
        log = (verify_log(path, registry_path=registry_path, strict=strict,
                          incremental=incremental, shown=shown, record=True)
               if path.exists() else LogReport(path=path, shown=shown))
        if witnesses:
            log.problems += _rewind_problems(path)
        report.logs.append(log)
    return report


def chain_problems(events: list[Event], registry_path: Path | None = None,
                   strict: bool = False) -> list[str]:
    """Certain problems in an in-memory event list, chain by chain (seal,
    checkpoint restore, prefix restore). Voids are resolved from the list itself.

    Only faults in the chain count here: a signature this machine has no key
    for leaves these callers exactly where they were before signatures were
    checked at all (a restore on a host without every key must still work),
    and a wrong clock is verify's to flag (LED-7) -- one wrong clock never
    stops conflict resolution, a prefix restore or a seal of the space.
    """
    from .voids import from_events
    voids = from_events(events)
    chains: dict = {}
    for event in events:
        chains.setdefault(getattr(event, "log", None) or event.actor, []).append(event)
    out: list[str] = []
    for name, chain in sorted(chains.items()):
        chain.sort(key=lambda e: e.seq if type(e.seq) is int else -1)
        for p in check_events(list(enumerate(chain, 1)), registry_path=registry_path,
                              strict=strict, voids=voids, log=name):
            if p.certain and p.kind != "clock":
                out.append(f"{name}: {p.text}")
    return out


def _signature_problem(actor: str, registry_path: Path | None, at_ms: int | None = None) -> str:
    """Why a signature did not verify, in words that separate the two causes.

    "unknown actor or invalid signature" could not tell a forged event from a
    rebuilt host whose new key was never registered (fleet sim 2026-10-03,
    break 12). Both phrasings keep the old words, which readers match on.
    """
    from .keys import _iso, key_at, key_history, known_verify_key
    base = f"signature verification failed for actor {actor!r} (unknown actor or invalid signature)"
    if not known_verify_key(actor, registry_path):
        return f"{base}: no registered key for {actor!r}"
    history = key_history(actor)
    expected = key_at(history, at_ms) if history else None
    named = f" ({expected[:12]}... at this event's time)" if expected else ""
    rotated = ""
    if len(history) > 1:
        rotated = " Registered keys: " + ", ".join(
            f"{k[:12]}... from {_iso(t) or 'the start'}" for t, k in history) + "."
    return (f"{base}: signed with a key that is not the key registered for {actor!r}{named} -- a rotated or "
            f"regenerated key (the owner approves it: ledger_keys_collect.py --rotate {actor} "
            f"--owner-approves; or restore the old one on its host), a retired key used after its "
            f"rotation, or an edit by another writer.{rotated}")


def _tail_seq(path: Path) -> int:
    """The highest seq in a log file, -1 when there is none."""
    tail = -1
    try:
        for line in path.read_text(errors="replace").splitlines():
            try:
                tail = max(tail, int(json.loads(line).get("seq", -1)))
            except (ValueError, TypeError, AttributeError):
                continue
    except OSError:
        pass
    return tail


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

    Two more ways to lose history that a seq mark alone misses (LED-2):
      * REWRITTEN -- an old event edited and every later hash recomputed (audit
        A#2) leaves a chain of the same length that verifies clean. The writer
        also witnesses the hash it wrote at that seq (`<log>.hash`,
        "<seq> <hash>"); a different hash at that seq in the log is a rewrite.
      * MISSING -- the whole log deleted. `path` may name a log that no longer
        exists; with a witness for it, that is reported, never skipped.
    """
    from .log import read_stop, witness_path
    hwm_path = witness_path(path)
    try:
        hwm = int(hwm_path.read_text().strip())
        if hwm < 0:
            raise ValueError("negative witness")
    except FileNotFoundError:
        # No witness -- but a stop record still remembers how far this
        # machine wrote when the ledger refused the log. A witness deleted
        # after that refusal (2026-09-27) is not a clean log.
        stop = read_stop(path)
        if stop is None:
            return []
        tail = _tail_seq(path)
        if stop["hwm"] is None or tail < stop["hwm"]:
            return [f"TRUNCATED: log ends at seq {tail} but this machine wrote up to seq "
                    f"{stop['hwm']} (recorded when the ledger stopped appends; the seq witness "
                    f"is gone). The owner repairs it: .datacore/docs/recovery.md"]
        return []
    except (OSError, ValueError):
        return ["sequence witness is unreadable or invalid; rewind status cannot be verified"]

    witnessed: tuple[int, str] | None = None
    hash_path = hwm_path.with_suffix(".hash")
    try:
        w_seq, w_hash = hash_path.read_text().split()
        witnessed = (int(w_seq), w_hash)
    except FileNotFoundError:
        pass                                # written before the hash witness existed
    except (OSError, ValueError):
        return ["hash witness is unreadable or invalid; rewrite status cannot be verified"]

    if not path.exists():
        return [f"MISSING: this machine wrote up to seq {hwm} to {path.name} but the "
                f"log is gone (witness: {hwm_path})"]

    tail = -1
    hashes: dict[int, str] = {}
    try:
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                seq = int(ev.get("seq", -1))
            except (ValueError, TypeError, AttributeError):
                continue
            tail = max(tail, seq)
            hashes.setdefault(seq, str(ev.get("hash", "")))
    except OSError:
        return ["log is unreadable; rewind status cannot be verified"]

    if tail < hwm:
        return [f"TRUNCATED: log ends at seq {tail} but this machine wrote up to "
                f"seq {hwm} — {hwm - tail} event(s) missing from the tail "
                f"(witness: {hwm_path})"]
    if witnessed and witnessed[0] in hashes and hashes[witnessed[0]] != witnessed[1]:
        return [f"REWRITTEN: the event at seq {witnessed[0]} is not the one this machine "
                f"wrote (hash {hashes[witnessed[0]][:12]}…, witnessed {witnessed[1][:12]}…) "
                f"— earlier history was edited and re-chained (witness: {hash_path})"]
    return []
