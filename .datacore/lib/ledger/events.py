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
        # Phase 4 (ledger upgrade, 2026-10-04; Lean LedgerSpec/Item.lean first).
        # item.archive hides an item without closing it (delete, archive or
        # refile out of a view); item.reopen brings a dismissed or archived item
        # back to open -- the ONLY event that does.
        "item.archive",
        "item.reopen",
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
        # CANCELLING A BAD EVENT (LED-4/LED-5, owner decision 2026-09-26). The
        # ledger is append-only, so a bad event is never removed: an authorised
        # actor who is not its writer appends a void naming it exactly
        # ({log, seq, hash, reason}); verify accepts the voided event and fold
        # ignores it. See ledger.voids.
        "ledger.void",
        # SIGNING-KEY ROTATION (owner decision 2026-10-03). A rebuilt host holds
        # a new key; the owner approves it explicitly and the approval is this
        # event, written by a writer other than the one rotated:
        # {actor, old_key, new_key, valid_from, reason}. See keys.approve_rotation.
        "key.rotate",
    }
)


# ── HUB (DIP-0034 October 2026 amendment, sections B and E) ─────────────────
# Kept in one block so parallel additions to EVENT_TYPES above merge cleanly.
#
# Leases are written ONLY by the hub (ledger.hub): item.lease grants an item to
# a holder until `expires_ms`, item.lease.renew extends it, and
# item.lease.conflict makes a conflict the fold found durable and visible. The
# fold rule (earliest hub sequence wins) is in ledger.fold.
#
# `hub` is an optional ENVELOPE field: a stable hub id, never a hostname. It is
# part of the hashed and signed body when present and absent from both when not,
# so every event written before it existed keeps its bytes, hash and signature
# (invariant 6: readers ignore what is absent).
#
# `domain` (audit finding 6, 2026-10-18) is the same kind of optional envelope
# field: `ledger/2 space=<id> hub=<id> log=<chain>`, stamped by every writer
# in a hub space. It is inside the hashed and signed body, so a signature binds
# the protocol version, the space, the hub and the exact log it was written
# for: a captured event cannot be replayed into another space, another hub or
# another of its writer's logs. Absent (protocol 1) on every older event, whose
# bytes therefore do not change. ledger.hub_admission checks it.
#
# item.lease.expire (audit finding 9) ends one lease attempt explicitly,
# written by the grantor before any regrant; it records the holder-log
# frontier the grantor held, so a result admitted after it is told apart.
HUB_EVENT_TYPES = frozenset({"item.lease", "item.lease.renew", "item.lease.expire", "item.lease.conflict"})
#
# THE OWNER'S RECORDS (audit finding 4). `hub.enable` (epoch 0: the grantor,
# the cutover manifest) and `hub.promote` (epoch n+1: the new grantor, the
# predecessor's fence and frontier) are signed by the space's owner in the
# owner's own log; who grants is derived from them, never from a config file.
# `hub.fence` is the grantor's own statement that it stopped: written in its
# log, it is the last event of its epoch that counts.
#
# `hub.resolve` (Codex re-audit, new defect 4): the owner's decision on an
# expired lease attempt whose holder never came back to say what happened. It
# frees the item for another grant; nothing else does but the holder's own
# release or result naming the attempt.
OWNER_EVENT_TYPES = frozenset({"hub.enable", "hub.promote", "hub.resolve"})
#
# `ledger.reject` (audit finding 11): the grantor's record that a signed event
# already in a writer's log is a REJECTED PROPOSAL -- kept so the chain stays
# continuous, with no effect on state, until an authorised voider cancels it
# with `ledger.void`. Nothing is deleted or re-signed.
#
# `hub.start` (round 5, amendment 7): the grantor's own statement, in its own
# log, that it began serving this space for an epoch. Written durably before
# its first answer, so a copy of the space taken at any later moment shows
# that this grantor has served, whatever the writers' clocks say: a hub
# without its private state that finds it runs the owner-cleared barrier, and
# a genuine first start (none there) does not. Folds to nothing.
#
# `ledger.compensate` (round 5, owner decision 2026-10-23): a FORWARD correction
# of an event a seal already covers. A sealed outcome never changes, so an
# error found after the seal is not reversed: this record points to the
# original (log, seq, hash) with a reason, and carries its own receipt; the
# original stays confirmed. The fold gives it no effect on item state; an
# effect in the world is a new action recorded as its own event.
EVENT_TYPES = EVENT_TYPES | HUB_EVENT_TYPES | OWNER_EVENT_TYPES | {"hub.fence", "hub.start", "ledger.reject",
                                                                   "ledger.compensate"}


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
    #: The hub this event belongs to ("" = none; see the HUB block above).
    hub: str = ""
    #: The signing domain ("" = protocol 1, none; see the HUB block above).
    domain: str = ""


def body_dict(seq: int, hlc: str, actor: str, type: str, payload: dict, prev: str,
              hub: str = "", domain: str = "") -> dict:
    """Assemble the hashable body of an event (everything but `hash`/`sig`).

    `prev` is included so `compute_hash` over this body chains to the prior
    event -- that is the entire mechanism of the hash chain. `hub` enters the
    body only when set, so an event without one hashes exactly as before.
    """
    body = {
        "seq": seq,
        "hlc": hlc,
        "actor": actor,
        "type": type,
        "payload": payload,
        "prev": prev,
    }
    if hub:
        body["hub"] = hub
    if domain:
        body["domain"] = domain
    return body


def event_body(e) -> dict:
    """The hashable body of an existing event (an `Event` or a parsed line dict)."""
    if isinstance(e, dict):
        return body_dict(e["seq"], e["hlc"], e["actor"], e["type"], e["payload"], e["prev"],
                         hub=e.get("hub") or "", domain=e.get("domain") or "")
    return body_dict(e.seq, e.hlc, e.actor, e.type, e.payload, e.prev, hub=getattr(e, "hub", "") or "",
                     domain=getattr(e, "domain", "") or "")


def canonical_bytes(d: dict) -> bytes:
    """Deterministic byte encoding of `d`: sorted keys, no extra whitespace."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def compute_hash(body: dict) -> str:
    """sha256 hex digest of `body`'s canonical bytes."""
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def to_line(e: Event) -> str:
    """Serialize an Event to a single canonical-JSON line (no trailing newline).

    An event without a hub serializes exactly as it did before the field existed.
    """
    d = asdict(e)
    for optional in ("hub", "domain"):
        if not d.get(optional):
            d.pop(optional, None)
    return canonical_bytes(d).decode("utf-8")


def from_line(s: str) -> Event:
    """Parse a line produced by `to_line` back into an Event."""
    return Event(**json.loads(s))
