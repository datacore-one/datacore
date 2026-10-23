"""Which corrections take effect: ONE rule for every reader (round 6).

WHY ONE FUNCTION. Round 5 decided the correction window in four places -- the
fold, the seal construction, the receipt and the generic verifier -- and each
re-derived it from whatever events it happened to hold. The seal construction
and the settled reader fold WITHOUT the seal records, so they could not see
that a reversed event was already sealed, and the receipt could; the verifier
used no window at all (Codex round 5, C3, H2). A rule computed from different
inputs by different readers drifts. Here it is computed once, from records
that every reader holds in the same form, and `ledger.fold.fold` calls it, so
everything that folds -- the hub, the seal, the settled view, receipts, the
app -- takes the same answer.

THE RECORDS. In a hub space the GRANTOR decides each correction once, when it
first admits it, and writes the decision in its own signed log:

  * `ledger.admit` -- a reversal (`ledger.void`) inside the correction window,
    or a valid forward correction (`ledger.compensate`), with the hub's
    admission times as evidence;
  * `ledger.reject` -- one that is not (outside the window, not the owner's,
    an unsealed original, a missing original, ...).

A correction takes effect only when a grantor's `ledger.admit` names it and no
`ledger.reject` does. Nothing else is read: not a seal (the settled reader
has none), not a writer's timestamp (H1), not the local principal registry
(C5) -- who may reverse is the space owner named in the owner's records
(`hub.enable`), passed in as `reversers`. So the same logs give the same
answer on every replica, as a chain's state function would.

The DECISION itself (`reversal_problem`, `compensation_problem`) is made by
the hub at admission, by its own clock, and nowhere else.

SPACES WITHOUT A HUB keep the old rule exactly (owner, 2026-10-23): a void by
an authorised principal of the registry, no window (`ledger.voids`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .events import Event
from .voids import (COMPENSATE_TYPE, REVERSAL_MAX_AGE_MS, VOID_TYPE, Voids, _target, _writer, body_hash,
                    from_events, log_stem, resolve as resolve_voids)

ADMIT_TYPE = "ledger.admit"
REJECT_TYPE = "ledger.reject"
CORRECTION_TYPES = frozenset({VOID_TYPE, COMPENSATE_TYPE})


@dataclass
class Finality:
    """What the records say about corrections, for one event set."""
    #: hashes a grantor refused at the front door (`ledger.reject`): no effect.
    rejected: frozenset = frozenset()
    #: the reversals that take effect (a `ledger.voids.Voids`).
    voids: Voids = field(default_factory=Voids)
    #: correction hash -> the grantor's `ledger.admit` payload.
    admitted: dict = field(default_factory=dict)
    #: correction hash -> what it did ("applied (...)" / "no-op (...)").
    outcomes: dict = field(default_factory=dict)


def _grantors(lease_grantors) -> dict:
    """{hub id: actors} from the fold's lease authority ({hub: {grantor: epoch}} or {hub: set})."""
    return {hub: set(actors) for hub, actors in (lease_grantors or {}).items()}


def _by_grantor(e: Event, grantors: dict) -> bool:
    """A record in a grantor's OWN log, by a grantor of the hub it names."""
    return ((getattr(e, "log", None) or e.actor) == e.actor
            and e.actor in grantors.get(getattr(e, "hub", "") or "", ()))


def _ref(p) -> tuple | None:
    return (log_stem(p.get("log")), p.get("seq"), p.get("hash")) if isinstance(p, dict) else None


def resolve(events: list[Event], *, lease_grantors=None, reversers=None, legacy_reversals=None) -> Finality:
    """THE correction rule. `lease_grantors` None: a space without a hub.

    `legacy_reversals`: the reversals the owner's activation record
    (`hub.enable`) froze -- in effect by the old rule when the space moved to
    the hub. Each keeps its effect; nothing later can add one."""
    if lease_grantors is None:
        if not any(e.type == VOID_TYPE for e in events):
            return Finality()
        return Finality(voids=from_events(events))
    grantors = _grantors(lease_grantors)
    rejected, admitted = set(), {}
    for e in events:
        if e.type not in (REJECT_TYPE, ADMIT_TYPE) or not _by_grantor(e, grantors):
            continue
        p = e.payload if isinstance(e.payload, dict) else {}
        ref = p.get("event") if isinstance(p.get("event"), dict) else {}
        if not isinstance(ref.get("hash"), str):
            continue
        if e.type == REJECT_TYPE:
            rejected.add(ref["hash"])
        else:
            admitted.setdefault(ref["hash"], p)
    rejected = frozenset(rejected)
    owners = frozenset(_writer(r) for r in (reversers or ()))
    outcomes: dict = {}
    pending = []
    legacy_voids = {(log_stem((r.get("void") or {}).get("log")), (r.get("void") or {}).get("seq"),
                     (r.get("void") or {}).get("hash")) for r in (legacy_reversals or ())}
    legacy_effect = {}
    held = {(log_stem(getattr(e, "log", None) or e.actor), e.seq, e.hash) for e in events}
    for r in legacy_reversals or ():
        t = r.get("target") or {}
        key = (log_stem(t.get("log")), t.get("seq"), t.get("hash"))
        if key in held:
            legacy_effect[key] = r.get("body_sha256")
    for e in events:
        if e.type not in CORRECTION_TYPES:
            continue
        if (log_stem(getattr(e, "log", None) or e.actor), e.seq, e.hash) in legacy_voids:
            outcomes[e.hash] = "applied (a reversal from before activation: the owner's hub.enable record keeps it)"
            continue
        rec = admitted.get(e.hash)
        if e.hash in rejected:
            outcomes[e.hash] = "no-op (rejected proposal: the grantor refused it at the front door)"
        elif rec is None or rec.get("kind") != ("reversal" if e.type == VOID_TYPE else "compensation"):
            outcomes[e.hash] = "no-op (no grantor admitted it: outside the correction rules, or not judged yet)"
        elif _ref((rec.get("target") or {})) != _ref(e.payload):
            outcomes[e.hash] = "no-op (the grantor's admission names another original)"
        elif e.type == COMPENSATE_TYPE:
            p = e.payload
            outcomes[e.hash] = f"applied (forward correction of {log_stem(p.get('log'))} seq {p.get('seq')})"
        else:
            pending.append(e)

    index = {(log_stem(getattr(e, "log", None) or e.actor), e.seq): e for e in events}
    # A REVERSAL CANCELS EXACTLY THE BODY IT PINS (Codex round 6, N1): one
    # whose pin is not the reversed event's body has no effect -- it may not
    # be called applied, nor take another reversal's effect away.
    kept = []
    for v in pending:
        t = _target(v.payload)
        target = index.get(t[0][:2]) if not isinstance(t, str) else None
        if not isinstance(t, str) and target is not None and t[1] is not None and t[1] != body_hash(target):
            outcomes[v.hash] = "no-op (its body pin is not the reversed event's body)"
        else:
            kept.append(v)
    pending = kept

    def authority(v: Event, target_log: str):
        if _writer(v.actor) not in owners:
            return f"{v.actor!r} is not the space's owner (owner records)"
        return None

    voids = resolve_voids([(getattr(v, "log", None) or v.actor, v) for v in pending],
                          lambda log, seq: index.get((log, seq)), authority=authority) if pending else Voids()
    for v in pending:
        why = voids.refusal_for(getattr(v, "log", None) or v.actor, v.seq)
        t = _target(v.payload)
        target = index.get(t[0][:2]) if not isinstance(t, str) else None
        if why:
            outcomes[v.hash] = f"no-op ({why})"
        elif target is None or not voids.applies(getattr(target, "log", None) or target.actor, target):
            outcomes[v.hash] = "no-op (it does not cancel the event it names)"
        else:
            p = v.payload
            outcomes[v.hash] = f"applied (reversal of {log_stem(p.get('log'))} seq {p.get('seq')})"
    # A hub space reports nothing as a chain problem: a correction without
    # effect is a recorded no-op (its outcome), never a broken log.
    return Finality(rejected=rejected, voids=Voids(effective={**legacy_effect, **voids.effective}),
                    admitted=admitted, outcomes=outcomes)


def effective_events(events: list[Event], fold_inputs: dict) -> list[Event]:
    """`events` (admitted) without what has no effect by the one correction
    rule: rejected proposals and reversed events. For readers that scan
    rather than fold (job health, check-ins; Codex round 6, N4). A space
    without a hub: the old rule's reversals."""
    fin = resolve(events, lease_grantors=(fold_inputs or {}).get("lease_grantors"),
                  reversers=(fold_inputs or {}).get("reversers"),
                  legacy_reversals=(fold_inputs or {}).get("legacy_reversals"))
    return [e for e in events if e.hash not in fin.rejected
            and not fin.voids.applies(getattr(e, "log", None) or e.actor, e)]


# ── the hub's decision, at admission, by its own clock ──────────────────────

def _covered(seal_payload: dict, e: Event) -> bool:
    wm = seal_payload.get("watermarks") if isinstance(seal_payload, dict) else None
    if not isinstance(wm, dict):
        return False
    key = (getattr(e, "log", None) or e.actor) if seal_payload.get("version") == 2 else e.actor
    return type(wm.get(key)) is int and type(e.seq) is int and e.seq <= wm[key]


def sealed(events: list[Event], sealers, e: Event) -> bool:
    """Does any seal by one of `sealers` (the grantors) in `events` cover `e`?"""
    sealers = set(sealers or ())
    return any(s.type == "ledger.seal" and s.actor in sealers and _covered(s.payload, e) for s in events)


def original(events: list[Event], payload) -> Event | None:
    """The event a correction names (log, seq, hash), if this copy holds exactly it."""
    t = _target(payload)
    if isinstance(t, str):
        return None
    (log, seq, digest), _ = t
    e = next((x for x in events if log_stem(getattr(x, "log", None) or x.actor) == log and x.seq == seq), None)
    return e if e is not None and e.hash == digest else None


def reversal_problem(v: Event, events: list[Event], *, owner: str, sealers, admitted_ms, now_ms: int,
                     max_age_ms: int = REVERSAL_MAX_AGE_MS) -> tuple[str, str] | None:
    """(code, why) when the hub must refuse reversal `v` now, else None.

    `admitted_ms(event) -> int | None`: when the HUB admitted an event (its own
    clock; None when it holds no record). The reversal itself is admitted
    `now_ms`. Outside the window: refused, `reversal_closed`."""
    t = _target(v.payload)
    if isinstance(t, str):
        return "malformed", f"ledger.void: {t}"
    if not owner or _writer(v.actor) != _writer(owner):
        return "not_authorized", (f"only the space's owner ({owner or 'none recorded'}) reverses, as its owner "
                                  "records name it")
    target = original(events, v.payload)
    if target is None:
        return "malformed", "the reversal names no event this space holds (log, seq, hash)"
    if _writer(target.actor) == _writer(v.actor):
        return "not_authorized", f"{v.actor!r} may not reverse its own event"
    (log, seq, _), pin = t
    if pin is not None and pin != body_hash(target):
        return "malformed", f"the reversal's body pin is not the body of {log} seq {seq}"
    if sealed(events, sealers, target):
        return "reversal_closed", (f"reverses {log} seq {seq}, which a seal already covers; a sealed outcome "
                                   "never changes -- correct it with a forward ledger.compensate")
    at = admitted_ms(target)
    if at is None:
        return "reversal_closed", (f"the hub holds no admission time for {log} seq {seq} (it arrived before this "
                                   "hub's record), so the correction window cannot be shown open; correct it "
                                   "after the seal with a forward ledger.compensate")
    if now_ms - at > max_age_ms:
        return "reversal_closed", (f"reverses {log} seq {seq} {(now_ms - at) // 1000} s after the hub admitted it, "
                                   f"past the correction window ({max_age_ms // 1000} s by the hub's clock); "
                                   "correct it with a forward ledger.compensate")
    return None


def compensation_problem(c: Event, events: list[Event], *, owner: str, sealers) -> tuple[str, str] | None:
    """(code, why) when forward correction `c` is not admissible, else None:
    a reason; an original this copy holds exactly; written by the space's
    owner or the original's writer; and the original SEALED before `c` (an
    unsealed original is still inside the correction window)."""
    from actor_identity import base_writer
    p = c.payload if isinstance(c.payload, dict) else {}
    if not isinstance(p.get("reason"), str) or not p["reason"].strip():
        return "bad_compensate", "a correction names its reason"
    orig = original(events, p)
    if orig is None:
        return "bad_compensate", "a correction names an event this space holds (log, seq, hash)"
    if base_writer(c.actor) not in {base_writer(owner or ""), base_writer(orig.actor)}:
        return "bad_compensate", "only the space's owner or the original's writer corrects it"
    if not any(s.type == "ledger.seal" and s.actor in set(sealers or ()) and _covered(s.payload, orig)
               and not _covered(s.payload, c) for s in events):
        return "bad_compensate", ("the original is not sealed yet: it is still inside the correction window, "
                                  "where a reversal (ledger.void) corrects it")
    return None


def admit_payload(e: Event, chain: str, kind: str, *, admitted_ms: int, target_admitted_ms=None) -> dict:
    """The grantor's `ledger.admit` record for correction `e` (its evidence)."""
    p = e.payload if isinstance(e.payload, dict) else {}
    out = {"event": {"log": chain, "seq": e.seq, "hash": e.hash, "actor": e.actor, "type": e.type},
           "kind": kind, "target": {"log": log_stem(p.get("log")), "seq": p.get("seq"), "hash": p.get("hash")},
           "admitted_ms": admitted_ms}
    if kind == "reversal":
        out["target_admitted_ms"] = target_admitted_ms
        out["window_ms"] = REVERSAL_MAX_AGE_MS
    return out
