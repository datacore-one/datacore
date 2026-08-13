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
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .events import Event
from .fold import fold


@dataclass(frozen=True)
class Seal:
    """A settled point: which events, and what they folded to."""

    watermarks: dict[str, int]   # actor -> highest seq included
    state_root: str
    hlc: str
    sequencer: str

    def includes(self, event: Event) -> bool:
        wm = self.watermarks.get(event.actor)
        return wm is not None and event.seq <= wm


def watermarks(events: list[Event]) -> dict[str, int]:
    """Highest seq seen per actor — the frontier this machine can attest to."""
    out: dict[str, int] = {}
    for e in events:
        if e.seq > out.get(e.actor, -1):
            out[e.actor] = e.seq
    return out


def latest_seal(events: list[Event]) -> Seal | None:
    """The most recent seal, by HLC.

    Ties are impossible in practice (one sequencer) but resolved by HLC anyway
    rather than by list order, because list order depends on merge arrival and
    would make `settled()` machine-dependent — the exact property a seal exists
    to remove.
    """
    seals = [e for e in events if e.type == "ledger.seal"]
    if not seals:
        return None
    newest = max(seals, key=lambda e: e.hlc)
    p = newest.payload or {}
    wm = p.get("watermarks") or {}
    return Seal(
        watermarks={str(k): int(v) for k, v in wm.items()},
        state_root=str(p.get("state_root") or ""),
        hlc=newest.hlc,
        sequencer=newest.actor,
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
    return [e for e in events if e.type != "ledger.seal" and seal.includes(e)]


def settled(events: list[Event]):
    """Folded state as of the latest seal. `None` when nothing is sealed."""
    seal = latest_seal(events)
    if seal is None:
        return None
    return fold(settled_events(events))


def _self_consistent(events: list[Event]) -> list[tuple[str, int]]:
    """(actor, seq) pairs that appear more than once with different hashes.

    A fork that arrives via sync lands INSIDE the event set the sequencer is
    about to certify. verify_seal recomputes the root from those same events,
    so it agrees with itself and reports success — certifying a history another
    machine will disagree with. That is the worst thing finality can do, so it
    is checked before the root comparison rather than after.
    """
    seen: dict[tuple[str, int], str] = {}
    bad: list[tuple[str, int]] = []
    for e in events:
        k = (e.actor, e.seq)
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
        return False, (f"FORKED LOG: {len(forked)} (actor, seq) pair(s) have two "
                       f"different events, e.g. {a} seq {sq}. A seal over a fork "
                       f"certifies a history other machines reject — refusing.")

    seal = latest_seal(events)
    if seal is None:
        return None, "no seal yet"

    # A seal naming an actor this machine has never seen cannot be verified
    # here — it is not wrong, we are behind. Say so rather than failing.
    known = watermarks(events)
    behind = [a for a, wm in seal.watermarks.items() if known.get(a, -1) < wm]
    if behind:
        return None, f"behind the seal for: {', '.join(sorted(behind))}"

    recomputed = fold(settled_events(events)).state_root()
    if recomputed == seal.state_root:
        n = sum(seal.watermarks.values()) + len(seal.watermarks)
        return True, f"seal by {seal.sequencer} verifies over ~{n} event(s)"
    return False, (f"SEAL MISMATCH: sequencer {seal.sequencer} claims "
                   f"{seal.state_root[:12]}, recomputed {recomputed[:12]}")


def build_seal_payload(events: list[Event]) -> dict:
    """What the sequencer appends. Excludes seals themselves, so a seal never
    seals a seal — that would make the root depend on sealing history rather
    than on the work, and two sequencer runs over identical work would differ.
    """
    work = [e for e in events if e.type != "ledger.seal"]
    return {
        "watermarks": watermarks(work),
        "state_root": fold(work).state_root(),
    }
