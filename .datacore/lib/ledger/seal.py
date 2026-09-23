"""Finality over the combined log (DIP-0042).

Appends need no sequencer: per-actor logs are disjoint files, so a merge is a
union and two actors both at seq 82 is correct, exactly as two Ethereum
accounts can both sit at nonce 82. What a sequencer provides is ordering
ACROSS actors — a point everyone can name as settled.

Without one, every read is a read of the TIP: whatever happened to have
arrived on this machine. Two boxes then hold different event sets and neither
is wrong, which is fine for a dashboard and useless for settlement — there is
no moment at which spend, ownership or completion can be called decided.

A seal is the sequencer saying:

    including exactly these per-actor sequence numbers, the folded state
    root was X

WHY WATERMARKS, NOT A COUNT OR A TIMESTAMP. The seal must be verifiable by
anyone, offline, without trusting the sequencer and without depending on the
order events happened to arrive in. Per-actor watermarks name an exact event
set: fold up to them, hash, compare to the claimed root. A count would be
ambiguous under concurrent appends and a wall-clock time would re-introduce
the clock problem the HLC exists to avoid.

WHAT A SEAL IS NOT. It is not consensus and not a vote. All five machines
belong to one principal and write disjoint files; there is no byzantine
participant to tolerate. The sequencer is a designated role (Winston), and a
wrong seal is DETECTABLE by every reader rather than authoritative — which is
the property that makes designating a single sequencer safe here.

ONLY THE SEQUENCER'S SEALS COUNT (owner decision L1, 2026-09-23). Readers —
`latest_seal`, `verify_seal`, `settled_events`, `settled` — consider only
`ledger.seal` events whose actor is the designated sequencer, `sequencer()`:
`$DATACORE_SEQUENCER`, default `winston`, the one setting `ledger_seal.py emit`
already used to decide who may seal. A seal by any other writer (a `--force`
seal from another box, a stray or hostile writer) is IGNORED: it neither
advances nor regresses settlement, and it is reported in `verify_seal`'s
detail rather than failing the ledger. Before this, the latest seal from ANY
writer was authoritative if it verified, so one writer could move settlement
by sealing. (Lean: DatacoreSpec/LedgerSeal.lean, `latest_is_sequencer`,
`foreign_seal_inert`.)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .events import Event, body_dict, canonical_bytes, compute_hash
from .fold import fold


@dataclass(frozen=True)
class Seal:
    """A settled point: which events, and what they folded to."""

    watermarks: dict[str, int]   # actor -> highest seq included
    state_root: str
    hlc: str
    sequencer: str
    version: int = 1
    event_set_hash: str = ""

    def includes(self, event: Event) -> bool:
        key = (getattr(event, "log", None) or event.actor) if self.version == 2 else event.actor
        wm = self.watermarks.get(key)
        return wm is not None and event.seq <= wm


DEFAULT_SEQUENCER = "winston"


def sequencer() -> str:
    """The designated sequencer, read at call time: `$DATACORE_SEQUENCER` or winston.

    One definition for the writer (`ledger_seal.py emit`) and every reader, so
    the role that may seal and the role whose seals are believed cannot drift.
    """
    return os.environ.get("DATACORE_SEQUENCER") or DEFAULT_SEQUENCER


def _seal_events(events: list[Event]) -> list[Event]:
    """The sequencer's seals. Every other writer's `ledger.seal` is inert."""
    seq = sequencer()
    return [e for e in events if e.type == "ledger.seal" and e.actor == seq]


def ignored_seals(events: list[Event]) -> dict[str, int]:
    """Seals by writers other than the sequencer, counted per writer (reported, never fatal)."""
    seq = sequencer()
    out: dict[str, int] = {}
    for e in events:
        if e.type == "ledger.seal" and e.actor != seq:
            out[e.actor] = out.get(e.actor, 0) + 1
    return out


def _ignored_note(events: list[Event]) -> str:
    ign = ignored_seals(events)
    if not ign:
        return ""
    who = ", ".join(sorted(ign))
    return (f"; ignored {sum(ign.values())} seal(s) by {who} "
            f"(not the sequencer {sequencer()})")


def watermarks(events: list[Event], *, per_log=True) -> dict[str, int]:
    """Highest seq seen per actor — the frontier this machine can attest to."""
    out: dict[str, int] = {}
    for e in events:
        key = (getattr(e, "log", None) or e.actor) if per_log else e.actor
        if e.seq > out.get(key, -1):
            out[key] = e.seq
    return out


def latest_seal(events: list[Event]) -> Seal | None:
    """The sequencer's most recent seal, by HLC. `None` if the sequencer has not sealed.

    Seals by any other writer are ignored (decision L1): see the module
    docstring and `ignored_seals`.

    Ties are impossible in practice (one sequencer) but resolved by HLC anyway
    rather than by list order, because list order depends on merge arrival and
    would make `settled()` machine-dependent — the exact property a seal exists
    to remove.
    """
    seals = _seal_events(events)
    if not seals:
        return None
    newest = max(seals, key=lambda e: e.hlc)
    p = newest.payload or {}
    wm = p.get("watermarks")
    if (not isinstance(wm, dict) or any(not isinstance(k, str) or not k or type(v) is not int or v < 0 for k, v in wm.items())
            or type(p.get("version", 1)) is not int or p.get("version", 1) not in {1, 2}):
        raise ValueError("invalid or unsupported seal frontier")
    return Seal(
        watermarks={str(k): int(v) for k, v in wm.items()},
        state_root=str(p.get("state_root") or ""),
        hlc=newest.hlc,
        sequencer=newest.actor,
        version=p.get("version", 1),
        event_set_hash=p.get("event_set_hash", ""),
    )


def settled_events(events: list[Event]) -> list[Event]:
    """Events at or before the latest seal. Empty list when nothing is sealed.

    An unsealed ledger has NO settled state — deliberately. Falling back to
    "treat the tip as settled" would make an unsealed system look identical to
    a sealed one, which is the failure this module exists to prevent.
    """
    seal = latest_seal(events)
    if seal is None:
        return []
    ok, detail = verify_seal(events)
    if ok is not True:
        raise ValueError("unverified settlement: " + detail)
    return _covered_events(events, seal)


def settled(events: list[Event]):
    """Folded state as of the latest seal. `None` when nothing is sealed."""
    seal = latest_seal(events)
    if seal is None:
        return None
    return fold(settled_events(events))


def _self_consistent(events: list[Event]) -> list[tuple[str, int]]:
    """(log, seq) pairs that appear more than once with different hashes.

    A fork that arrives via sync lands INSIDE the event set the sequencer is
    about to certify. verify_seal recomputes the root from those same events,
    so it agrees with itself and reports success — certifying a history another
    machine will disagree with. That is the worst thing finality can do, so it
    is checked before the root comparison rather than after.

    THE KEY IS THE LOG FILE, NOT THE ACTOR. It read `(actor, seq)` until
    2026-09-08, which was right while a writer owned exactly one file. It stopped
    being right when datacore#148 gave a run branch its own `<actor>-run-<date>`
    log: `seq` restarts at 0 per file, so one actor legitimately has several
    events at seq 0 — different events, different hashes, no fork. The stricter
    key turned that design into a permanent false alarm; measured on 5-plur it
    reported 88 forked pairs, every one of them a base log against its own
    run-branch sibling and not one between two independent writers.

    Weakening it costs nothing real. Two machines that genuinely fork a chain
    write the SAME file name, so they still collide here. What is no longer
    flagged is a collision across two different files, which is not a fork:
    `verify_chain` takes one path and checks linkage within it, so the chain —
    and therefore the identity of `seq` — is scoped to the file.

    Events with no `log` attribute (constructed in memory rather than read from
    disk) fall back to the actor, which is exactly the old behavior.
    """
    seen: dict[tuple[str, int], str] = {}
    bad: list[tuple[str, int]] = []
    for e in events:
        k = (getattr(e, "log", None) or e.actor, e.seq)
        if k in seen and seen[k] != e.hash:
            bad.append(k)
        seen[k] = e.hash
    return sorted(set(bad))


def verify_seal(events: list[Event]) -> tuple[bool | None, str]:
    """Recompute the sealed state and compare to the sequencer's claim.

    Returns (None, reason) when there is nothing to check — an unsealed ledger
    is not a failing one. This is the check that makes a designated sequencer
    safe: its claim is reproducible by every reader.
    """
    forked = _self_consistent(events)
    if forked:
        a, sq = forked[0]
        return False, (f"FORKED LOG: {len(forked)} (log, seq) pair(s) have two "
                       f"different events, e.g. log {a} seq {sq}. A seal over a "
                       f"fork certifies a history other machines reject — refusing.")

    try:
        seal = latest_seal(events)
    except (ValueError, TypeError, AttributeError):
        return False, "invalid or unsupported seal"
    if seal is None:
        return None, "no seal yet" + _ignored_note(events)
    if seal.version != 2:
        return None, ("legacy seal has an ambiguous actor frontier; re-seal with version 2"
                      + _ignored_note(events))

    # FINALITY DOES NOT RUN BACKWARDS. The latest seal was chosen by HLC from
    # any writer (only the sequencer's since decision L1), so a later seal
    # naming LOWER watermarks -- a sequencer that sealed from a lagging or
    # rewound checkout, or (before L1) a `--force` seal from another box --
    # used to verify cleanly while events an earlier seal had
    # settled silently left settled state. "A point everyone can name as
    # settled" is worthless if the next seal can un-settle it, so a regression
    # against any earlier version-2 seal is a wrong seal, and wrong seals are
    # what every reader must detect. Found by the Lean model
    # (DatacoreSpec/LedgerSeal.lean, seal_*), replayed 2026-09-23.
    regressed = seal_regressions(events, seal)
    if regressed:
        return False, ("SEAL REGRESSION: the latest seal un-settles events an "
                       "earlier seal settled, e.g. " + regressed[0]
                       + " — finality must only move forward")

    # A seal naming an actor this machine has never seen cannot be verified
    # here — it is not wrong, we are behind. Say so rather than failing.
    known = watermarks(events)
    behind = [a for a, wm in seal.watermarks.items() if known.get(a, -1) < wm]
    if behind:
        return None, f"behind the seal for: {', '.join(sorted(behind))}"

    if _chain_issue([event for event in events if seal.includes(event)]):
        return False, "SEAL MISMATCH: incomplete or invalid covered log chain"
    covered = _covered_events(events, seal)
    if _event_set_hash(covered) != seal.event_set_hash:
        return False, "SEAL MISMATCH: covered event set differs"
    for event in covered:
        body = body_dict(event.seq, event.hlc, event.actor, event.type, event.payload, event.prev)
        if compute_hash(body) != event.hash:
            return False, "SEAL MISMATCH: invalid event hash"
    recomputed = fold(covered).state_root()
    if recomputed == seal.state_root:
        n = sum(seal.watermarks.values()) + len(seal.watermarks)
        # COVERAGE IS NOT CORRECTNESS. A seal is internally consistent as long
        # as its root matches the events it names — so a sequencer that simply
        # NEVER names an actor produces a seal that verifies forever while that
        # actor's work is permanently outside settled state. Nothing is forged;
        # a writer is just quietly ignored, which is the same outcome.
        #
        # Lagging coverage is NORMAL — the sequencer seals what it has synced —
        # so this is reported, never failed. But it must be SAID: an unnamed
        # actor is invisible in settled state, and silence is how that becomes
        # permanent.
        known = watermarks([e for e in events if e.type != "ledger.seal"])
        uncovered = sorted(a for a in known if a not in seal.watermarks)
        behind_actors = sorted(
            a for a, wm in seal.watermarks.items() if known.get(a, -1) > wm)
        note = ""
        if uncovered:
            note += f"; NOT COVERED: {', '.join(uncovered)}"
        if behind_actors:
            note += f"; lags newer events from {', '.join(behind_actors)}"
        note += _ignored_note(events)
        return True, f"seal by {seal.sequencer} verifies over ~{n} event(s){note}"
    return False, (f"SEAL MISMATCH: sequencer {seal.sequencer} claims "
                   f"{seal.state_root[:12]}, recomputed {recomputed[:12]}")


def regressions(earlier: dict[str, int], later: dict[str, int]) -> list[str]:
    """Logs whose watermark `later` lowers or drops relative to `earlier`.

    Empty exactly when `later` dominates `earlier` key by key, which is what
    makes the later seal's settled set a superset of the earlier one's.
    """
    return [f"{k}: {v} -> {later[k] if k in later else 'absent'}"
            for k, v in sorted(earlier.items(), key=lambda kv: str(kv[0]))
            if type(v) is int and later.get(k, -1) < v]


def seal_regressions(events: list[Event], seal: Seal) -> list[str]:
    """How `seal` regresses against every EARLIER version-2 sequencer seal in `events`.

    Only version-2 predecessors are compared: a version-1 frontier is keyed by
    actor, not log, and is already reported as ambiguous. A predecessor whose
    frontier does not parse never settled anything and is skipped. Seals by
    other writers never settled anything either (decision L1) and are skipped.
    """
    out: list[str] = []
    for e in _seal_events(events):
        if not (e.hlc < seal.hlc):
            continue
        p = e.payload or {}
        wm = p.get("watermarks")
        if p.get("version") != 2 or not isinstance(wm, dict):
            continue
        if any(not isinstance(k, str) or type(v) is not int for k, v in wm.items()):
            continue
        out += regressions(wm, seal.watermarks)
    return out


def build_seal_payload(events: list[Event]) -> dict:
    """What the sequencer appends. Excludes seals themselves, so a seal never
    seals a seal — that would make the root depend on sealing history rather
    than on the work, and two sequencer runs over identical work would differ.
    """
    if _self_consistent(events) or _chain_issue(events):
        raise ValueError("cannot seal an incomplete or invalid log chain")
    work = [e for e in events if e.type != "ledger.seal"]
    return {
        "version": 2,
        "event_set_hash": _event_set_hash(work),
        "watermarks": watermarks(work),
        "state_root": fold(work).state_root(),
    }


def _covered_events(events, seal):
    return [event for event in events if event.type != "ledger.seal" and seal.includes(event)]


def _event_set_hash(events):
    import hashlib
    rows = [{"log": getattr(e, "log", None) or e.actor, "seq": e.seq, "hash": e.hash,
             "body": body_dict(e.seq, e.hlc, e.actor, e.type, e.payload, e.prev)} for e in events]
    rows.sort(key=lambda row: (row["log"], row["seq"], row["hash"]))
    return hashlib.sha256(canonical_bytes({"version": 2, "events": rows})).hexdigest()


def _chain_issue(events):
    """Validate complete chain prefixes, including earlier seal events."""
    chains = {}
    for event in events:
        chains.setdefault(getattr(event, "log", None) or event.actor, []).append(event)
    for chain in chains.values():
        expected_seq, expected_prev = 0, "GENESIS"
        for event in sorted(chain, key=lambda event: event.seq):
            body = body_dict(event.seq, event.hlc, event.actor, event.type, event.payload, event.prev)
            if event.seq != expected_seq or event.prev != expected_prev or compute_hash(body) != event.hash:
                return True
            expected_seq, expected_prev = event.seq + 1, event.hash
    return False
