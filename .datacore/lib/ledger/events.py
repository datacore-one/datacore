"""Event model: canonical bytes, hash chain.

An Event is the atomic unit of the append-only ledger. Its `hash` is a
sha256 digest of the canonical (deterministic) byte encoding of its body --
and that body includes `prev` (the hash of the preceding event), which is
what turns a flat list of events into a hash chain: mutating any earlier
event changes its hash, which no longer matches the `prev` recorded by the
event after it.

This module is pure data modeling: no I/O, no clock reads, no crypto. It
does not sign or verify events -- Task 1.4 (writer) signs `canonical_bytes`
output at write time, and Task 1.5 (verifier) re-derives hashes to check
chain integrity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

EVENT_TYPES = frozenset(
    {
        "item.create",
        "item.claim",
        "item.release",
        "item.complete",
        "item.verify",
        "item.dismiss",
        # item.update carries CHANGED FIELDS for an existing item.
        # Without it the vocabulary can create an item and close it and
        # nothing in between, so a task rescheduled in org could never be
        # reflected: a second item.create on a known id is a no-op by
        # design. Phase 0 needs this to keep the projection equal to org,
        # and the target design needs it just as much -- an agent moving a
        # deadline is a fact, not a re-creation.
        "item.update",
        "owner.set",
        "spend.record",
        # FINALITY (DIP-0042). Everything above is an append by one actor about
        # its own work; per-actor logs are disjoint, so appends need no
        # sequencer. `ledger.seal` is the one event that is ABOUT the combined
        # log: the sequencer attests "including exactly these per-actor
        # sequence numbers, the folded state root was X".
        #
        # This is the block-time distinction. Without it every read is a read of
        # the TIP — whatever happens to have arrived on this machine — so two
        # boxes can disagree and neither is wrong, and there is no point anyone
        # can name as settled. A seal creates that point: state at or before the
        # watermark is final and identical everywhere; state after it is the
        # tip, useful for a UI and not for settlement.
        #
        # It carries watermarks rather than a count so verification is
        # independent of arrival order: fold the events up to those seqs, hash,
        # compare. Anyone can check the sequencer's claim without trusting it.
        "ledger.seal",
        "metric.attest",
        "artifact.attest",
        "policy.set",
        "approval.grant",
        # Migration (DIP-0043). item.clock.* exist because clocking is
        # genuinely concurrent -- two machines can clock the same task -- so it
        # must be events rather than an opaque payload blob that would
        # last-writer-wins. projection.attest lets machines compare what they
        # rendered without a network call: the ledger is its own comparison
        # channel.
        "item.clock.start",
        "item.clock.stop",
        "projection.attest",
        # Claim-grant handshake (DIP-0034 amendment): a claim is a PROPOSAL;
        # execution in an arbitrated pool requires the arbiter's grant.
        "item.grant",
    }
)


@dataclass
class Event:
    seq: int
    hlc: str
    actor: str
    type: str
    payload: dict
    prev: str
    hash: str
    sig: str


def body_dict(seq: int, hlc: str, actor: str, type: str, payload: dict, prev: str) -> dict:
    """Assemble the hashable body of an event (everything but `hash`/`sig`).

    `prev` is included so `compute_hash` over this body chains to the prior
    event -- that is the entire mechanism of the hash chain.
    """
    return {
        "seq": seq,
        "hlc": hlc,
        "actor": actor,
        "type": type,
        "payload": payload,
        "prev": prev,
    }


def canonical_bytes(d: dict) -> bytes:
    """Deterministic byte encoding of `d`: sorted keys, no extra whitespace."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def compute_hash(body: dict) -> str:
    """sha256 hex digest of `body`'s canonical bytes."""
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def to_line(e: Event) -> str:
    """Serialize an Event to a single canonical-JSON line (no trailing newline)."""
    return canonical_bytes(asdict(e)).decode("utf-8")


def from_line(s: str) -> Event:
    """Parse a line produced by `to_line` back into an Event."""
    return Event(**json.loads(s))
