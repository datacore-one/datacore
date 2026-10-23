"""One admission rule for a hub space (DIP-0034 October amendment D; Codex audit
2026-10-18, findings 1, 2, 6 and 7).

WHY ONE RULE. The hub used to check an event only when it arrived over HTTP.
The same event arriving by git, a restore or a rebuild went straight into the
fold, and the fold's only test for "written by a hub" was a non-empty `hub`
field -- which every writer in a hub space stamps. So an ordinary writer could
write itself a lease, publish it by git, and every rebuild would honour it
(finding 2). And "legacy, valid unsigned" was decided by comparing the event's
OWN timestamp with the cutover, so anyone could backdate an unsigned event past
the signing rule (finding 1).

Now every path asks this module the same question about the same event:

  * `check_event`  -- one post-cutover event against the event before it in its
                      log (HTTP append, and each step of `admit`);
  * `admit_chain`  -- one log, read from disk: the legacy prefix the cutover
                      manifest names, then every later event through
                      `check_event`. The first refused event ends what is
                      admitted of that log (fail closed: nothing after a bad
                      event can be trusted to chain onto it);
  * `admit`        -- a whole space (the hub's state, a rebuild from git, what
                      the hub seals).

THE RULE FOR A POST-CUTOVER EVENT
  1. well-formed fields and a known type;
  2. its log belongs to its writer (`base_writer`), and hub-only types
     (leases, renewals, lease conflicts, seals) only from an authorised grantor
     in that grantor's own log -- an ordinary signature confers no hub role;
  3. signed, chained exactly onto the previous event (seq, prev, hash), and the
     signature verifies against a key this machine holds for the writer at the
     event's time. No key here means NOT admitted (strict; post-cutover never
     fails open);
  4. the event names this hub and carries this space's signing domain
     (`signing_domain`: protocol, space id, hub id, log), so a signed event
     captured in another space, hub or log is refused;
  5. a well-formed HLC, not beyond `now_ms` + the writer's own future tolerance,
     and after the previous event in its own log.

LEGACY is membership in the owner's cutover manifest (`Authority.legacy`: per
log, the highest seq and head hash at cutover). A legacy prefix is admitted
only when this copy holds it in full and it hashes, links and ends exactly at
the manifest's head; a timestamp never makes anything legacy. Network
admission never admits legacy at all: it needs a valid signature (the hub's
HTTP append refuses unsigned events before asking this module).

PURE. No clock read (`now_ms` is an input), no config read (`Authority` is an
input): the caller decides who the grantors are BEFORE replay starts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .events import EVENT_TYPES, HUB_EVENT_TYPES, OWNER_EVENT_TYPES, POOL_EVENT_TYPES, Event, canonical_bytes, compute_hash, event_body

#: The protocol a domain-bound event speaks. Events without a domain are protocol 1.
PROTOCOL = 2
#: Every protocol THIS code reads and writes correctly (audit finding 17). A
#: hub space declares its protocol; a host whose code lacks it refuses the
#: space (`ProtocolUnsupported`) rather than reading the envelope with a
#: closed field list and mis-hashing it.
SUPPORTED_PROTOCOLS = frozenset({1, 2})


class ProtocolUnsupported(RuntimeError):
    """This host's code does not support the protocol the space declares."""


def require_protocol(declared, where: str = "this space") -> None:
    if type(declared) is not int or declared not in SUPPORTED_PROTOCOLS:
        raise ProtocolUnsupported(
            f"{where} declares ledger protocol {declared!r}; this host supports "
            f"{sorted(SUPPORTED_PROTOCOLS)}. Upgrade this host before it reads or writes the space "
            "(DIP-0034 amendment I: every reader and writer must support the space's protocol)")


_PROTOCOL_CACHE: dict = {}


def check_space_protocol(space_dir) -> None:
    """Refuse a hub space whose declared protocol this code does not support.
    Cheap enough for every read: the config is parsed once per change."""
    import json
    path = Path(space_dir) / ".datacore" / "ledger-hub.json"
    try:
        st = path.stat()
    except FileNotFoundError:
        return
    key = (str(path), st.st_mtime_ns, st.st_size)
    declared = _PROTOCOL_CACHE.get(key)
    if declared is None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return                     # load_config reports a broken config to writers
        declared = data.get("protocol", PROTOCOL) if isinstance(data, dict) else PROTOCOL
        if isinstance(data, dict) and data.get("enabled") is False:
            declared = 1
        _PROTOCOL_CACHE[key] = declared
    require_protocol(declared, str(path.parent.parent))
#: Written only by a hub's grantor, in its own log (`pool.budget`: the role
#: pool's daily threshold, amendment G; `hub.start`: round 5, amendment 7;
#: `ledger.admit`: round 6, a correction the grantor admitted).
HUB_ONLY_TYPES = HUB_EVENT_TYPES | POOL_EVENT_TYPES | {"ledger.seal", "hub.fence", "hub.start", "ledger.reject",
                                                       "ledger.admit"}
#: Refusals that make a signed, chained event a REJECTED PROPOSAL rather than
#: the end of its log, once the grantor has recorded it (`ledger.reject`,
#: audit finding 11). The fold gives such an event no effect: a hub-only type
#: by a non-grantor is not a grantor's, an owner type by a non-owner is not
#: the owner's record, an unknown type folds to nothing.
PROPOSAL_CODES = frozenset({"hub_only_type", "owner_only", "unknown_type"})
_HLC = re.compile(r"[0-9]{13}\.[0-9]{4}\.[a-z0-9][a-z0-9_-]*")
GENESIS = "GENESIS"


def signing_domain(space_id: str, hub_id: str, chain: str) -> str:
    """The domain an event in `chain` of this space and hub signs over."""
    return f"ledger/{PROTOCOL} space={space_id} hub={hub_id} log={chain}"


@dataclass(frozen=True)
class Authority:
    """Everything admission depends on besides the events: explicit input."""
    space_id: str
    hub_id: str
    #: Actors whose hub-only events count (the grantor and any it succeeded).
    grantors: frozenset
    #: The cutover manifest: {chain: (highest legacy seq, head hash)}.
    legacy: Mapping[str, tuple[int, str]] = field(default_factory=dict)
    registry_path: Path | None = None
    #: The owner whose signed records (hub.enable, hub.promote) define the epochs.
    owner: str = ""
    #: One `Epoch` per grantor, oldest first (audit finding 4). Empty: one
    #: epoch 0 for every grantor (spaces enabled before the records existed).
    epochs: tuple = ()
    #: THE ACTIVATION RECORD'S REVERSALS (round 6, owner: every space moves to
    #: the hub at once): the reversals that were in effect, by the old rule,
    #: when the owner activated the space -- frozen in the owner-signed
    #: `hub.enable`. Each keeps its effect for good; every later reversal
    #: follows the correction window. ({"void", "target", "body_sha256"} dicts.)
    legacy_reversals: tuple = ()

    def lease_grantors(self) -> dict:
        """The fold's explicit lease authority (ledger.fold.fold): {hub: {grantor: epoch}}."""
        if self.epochs:
            return {self.hub_id: {ep.grantor: ep.epoch for ep in self.epochs}}
        return {self.hub_id: self.grantors}

    def reversers(self) -> frozenset:
        """Who may reverse in this space (round 6, owner decision C5): the owner
        the owner's own signed records name -- read from the ledger, never
        from the local principal registry."""
        return frozenset({self.owner}) if self.owner else frozenset()

    def fold_inputs(self, policy) -> dict:
        """The fold's explicit inputs for this space: lease authority, policy,
        who may reverse, and the reversals the activation record keeps. Every
        reader of a hub space folds with these."""
        return {"lease_grantors": self.lease_grantors(), "policy": policy, "reversers": self.reversers(),
                "legacy_reversals": self.legacy_reversals}

    def excused(self) -> dict:
        """{(log, seq, stored hash): body pin} the activation record's reversals cancel."""
        out = {}
        for r in self.legacy_reversals:
            t = r.get("target") or {}
            out[(str(t.get("log")), t.get("seq"), t.get("hash"))] = r.get("body_sha256")
        return out

    def epoch_of(self, grantor: str):
        return next((ep for ep in self.epochs if ep.grantor == grantor), None)

    @property
    def active(self):
        return self.epochs[-1] if self.epochs else None


@dataclass(frozen=True)
class Epoch:
    """One grantor's term, from the owner's records."""
    epoch: int
    grantor: str
    #: The new grantor grants nothing before this (its clock): every lease of
    #: the previous epoch has expired, plus the clock uncertainty.
    not_before_ms: int | None = None
    #: The last seq of this grantor's log that counts (its fence frontier),
    #: once a successor's promotion names it. None: still active.
    fence_seq: int | None = None
    fence_head: str | None = None
    #: This grantor's last seal as the successor's promotion names it
    #: ({seq, hash} in this grantor's log). Every successor seal covers at
    #: least what it covered (Codex re-audit, finding 4).
    last_seal: tuple | None = None


class Refusal(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


@dataclass
class Admission:
    events: list            # admitted, in input order
    refused: dict           # {chain: "seq N: code: message"} -- where each log stops


def _writer_of(chain: str) -> str:
    from actor_identity import base_writer
    from .log import TELEMETRY_SUFFIX
    return base_writer(chain[: -len(TELEMETRY_SUFFIX)] if chain.endswith(TELEMETRY_SUFFIX) else chain)


def _physical(hlc: str) -> int | None:
    return int(hlc.split(".", 1)[0]) if isinstance(hlc, str) and _HLC.fullmatch(hlc) else None


def check_event(event: Event, chain: str, prior: Event | None, auth: Authority, *,
                now_ms: int, current_key: bool = False, as_proposal: bool = False) -> None:
    """Raise `Refusal` unless `event` is admissible as the event after `prior` in `chain`.

    `current_key`: also require the signature to verify under the key the
    writer holds NOW (`now_ms`), not only at the event's own time -- the network
    front door's rule, so a key rotated out cannot sign new events by
    backdating them (finding 5).
    """
    from .log import FUTURE_TOLERANCE_MS
    from .verify import check_events
    from actor_identity import base_writer
    if (type(event.seq) is not int or event.seq < 0 or not isinstance(event.payload, dict)
            or not all(isinstance(v, str) for v in (event.hlc, event.actor, event.type, event.prev,
                                                     event.hash, event.sig, event.hub, event.domain))):
        raise Refusal("malformed", "invalid event field types")
    if event.type not in EVENT_TYPES and not as_proposal:
        raise Refusal("unknown_type", f"unknown event type {event.type!r}")
    if _writer_of(chain) != base_writer(event.actor):
        raise Refusal("foreign_log", f"{event.actor!r} may append only to its own log, not {chain!r}")
    if event.type in HUB_ONLY_TYPES and not (event.actor in auth.grantors and chain == event.actor) \
            and not as_proposal:
        raise Refusal("hub_only_type", f"{event.type} is written only by this space's grantor "
                                       f"({', '.join(sorted(auth.grantors))}), in its own log")
    if event.type in OWNER_EVENT_TYPES and not (auth.owner and event.actor == auth.owner and chain == event.actor) \
            and not as_proposal:
        raise Refusal("owner_only", f"{event.type} is written only by this space's owner, in its own log")
    ep = auth.epoch_of(event.actor) if chain == event.actor else None
    if ep is not None:
        # FENCING (audit finding 4): once the owner's promotion names this
        # grantor's frontier, nothing after it counts -- an isolated old hub
        # that kept granting confers nothing anywhere the promotion is known.
        if ep.fence_seq is not None and type(event.seq) is int and event.seq > ep.fence_seq:
            raise Refusal("fenced", f"{event.actor} was succeeded at seq {ep.fence_seq} (epoch {ep.epoch}); "
                                    f"seq {event.seq} is after its fence")
        if ep.fence_seq is not None and event.seq == ep.fence_seq and ep.fence_head and event.hash != ep.fence_head:
            raise Refusal("fenced", f"{event.actor} seq {event.seq} is not the frontier the promotion names")
        if event.type == "item.lease" and isinstance(event.payload, dict):
            if event.payload.get("epoch", 0) != ep.epoch:
                raise Refusal("wrong_epoch", f"{event.actor} grants for epoch {ep.epoch}, not "
                                             f"{event.payload.get('epoch')!r}")
            granted = event.payload.get("granted_ms")
            if ep.not_before_ms is not None and isinstance(granted, int) and granted < ep.not_before_ms:
                raise Refusal("too_early", f"epoch {ep.epoch} may grant only from {ep.not_before_ms}")
    if not event.sig:
        raise Refusal("unsigned", "the hub accepts only signed events (amendment D)")
    problems = [p for p in check_events(
        [(event.seq + 1, event)], registry_path=auth.registry_path, strict=True, log=chain,
        expected_prev=prior.hash if prior is not None else GENESIS,
        expected_seq=prior.seq + 1 if prior is not None else 0) if p.kind != "clock"]
    if problems:
        texts = [p.text for p in problems]
        code = ("bad_signature" if any("signature" in t for t in texts)
                else "bad_hash" if any("hash mismatch" in t for t in texts) else "bad_chain")
        raise Refusal(code, "; ".join(texts))
    if event.hub != auth.hub_id:
        raise Refusal("wrong_hub", f"event belongs to hub {event.hub!r}, this is {auth.hub_id!r}")
    expected_domain = signing_domain(auth.space_id, auth.hub_id, chain)
    if event.domain != expected_domain:
        raise Refusal("wrong_domain", f"event was signed for {event.domain or 'no domain'!r}, "
                                      f"not {expected_domain!r}")
    physical = _physical(event.hlc)
    if physical is None:
        raise Refusal("bad_clock", f"malformed hlc {event.hlc!r}")
    if physical > now_ms + FUTURE_TOLERANCE_MS:
        raise Refusal("bad_clock", f"hlc {event.hlc!r} is {(physical - now_ms) // 60000} min ahead of "
                                   "the hub's clock (wrong clock)")
    if prior is not None and not (event.hlc > prior.hlc):
        raise Refusal("bad_clock", f"hlc {event.hlc!r} does not follow {chain}'s previous event "
                                   f"({prior.hlc!r})")
    if current_key:
        from .keys import verify
        if not verify(event.actor, canonical_bytes(event_body(event)), event.sig,
                      registry_path=auth.registry_path, at_ms=now_ms):
            raise Refusal("bad_signature", f"{event.actor!r} signed with a key that is not its current key")


def _legacy_prefix(chain: str, events: list[Event], entry, excused=None) -> int:
    """How many leading events of `chain` the cutover manifest admits (raises if it does not match)."""
    try:
        top, head = int(entry[0]), str(entry[1])
    except (TypeError, ValueError, IndexError):
        raise Refusal("bad_manifest", f"unreadable cutover entry for {chain}") from None
    if len(events) <= top:
        raise Refusal("not_legacy", f"the cutover manifest names {chain} up to seq {top}; this copy "
                                    f"holds {len(events)} event(s)")
    prev = GENESIS
    excused = excused or {}
    for i, e in enumerate(events[: top + 1]):
        computed = compute_hash(event_body(e))
        # A pre-activation event a pre-activation reversal cancelled (the old
        # cleanup of bad events) stays in the chain by its stored hash, as the
        # old rule kept it; only that exact body is excused (round 6).
        key = (chain, e.seq, e.hash)
        intact = computed == e.hash or (key in excused and excused[key] == computed)
        if e.seq != i or e.prev != prev or not intact:
            raise Refusal("not_legacy", f"{chain} seq {i} is not the history the cutover manifest names")
        prev = e.hash
    if prev != head:
        raise Refusal("not_legacy", f"{chain} seq {top} is not the head the cutover manifest names")
    return top + 1


def admit_chain(chain: str, events: list[Event], auth: Authority, *, now_ms: int,
                rejected=frozenset()) -> tuple[int, str | None]:
    """(how many leading events of `chain` are admitted, why the next is not -- or None).

    `events` in seq order, as they lie in the log file. `rejected`: the
    (chain, seq, hash) the grantor recorded as rejected proposals; such an
    event stays in the chain (no effect on state) instead of ending it."""
    n, prior = 0, None
    try:
        if chain in auth.legacy:
            n = _legacy_prefix(chain, events, auth.legacy[chain], auth.excused())
            prior = events[n - 1] if n else None
        for e in events[n:]:
            try:
                check_event(e, chain, prior, auth, now_ms=now_ms)
            except Refusal as exc:
                if exc.code not in PROPOSAL_CODES or (chain, e.seq, e.hash) not in rejected:
                    raise
                check_event(e, chain, prior, auth, now_ms=now_ms, as_proposal=True)
            n, prior = n + 1, e
    except Refusal as exc:
        where = events[n].seq if n < len(events) else "-"
        return n, f"seq {where}: {exc.code}: {exc.message}"
    return n, None


def admit(events: list[Event], auth: Authority, *, now_ms: int) -> Admission:
    """The admissible part of a whole space's events (as `read_events` returns them)."""
    chains: dict[str, list[Event]] = {}
    for e in events:
        chains.setdefault(getattr(e, "log", None) or e.actor, []).append(e)
    keep: set[int] = set()
    refused: dict[str, str] = {}
    rejected: set = set()
    # The grantors' logs first: their `ledger.reject` records decide which
    # writer events are kept as rejected proposals (audit finding 11).
    for chain in sorted(chains, key=lambda c: (c not in auth.grantors, c)):
        ordered = sorted(chains[chain], key=lambda e: e.seq if type(e.seq) is int else -1)
        n, why = admit_chain(chain, ordered, auth, now_ms=now_ms, rejected=frozenset(rejected))
        keep.update(id(e) for e in ordered[:n])
        if why:
            refused[chain] = why
        if chain in auth.grantors:
            rejected |= rejected_proposals(ordered[:n])
    return Admission([e for e in events if id(e) in keep], refused)


def rejected_proposals(grantor_events) -> set:
    """{(chain, seq, hash)} named by a grantor's admitted `ledger.reject` records."""
    out = set()
    for e in grantor_events:
        ref = (e.payload or {}).get("event") if e.type == "ledger.reject" and isinstance(e.payload, dict) else None
        if isinstance(ref, dict) and isinstance(ref.get("log"), str) and type(ref.get("seq")) is int \
                and isinstance(ref.get("hash"), str):
            out.add((ref["log"], ref["seq"], ref["hash"]))
    return out
