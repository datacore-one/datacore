"""In-ledger void records: the only way a bad event is cancelled (LED-4, LED-5).

WHY. The ledger is append-only, so an event that fails its own checks -- a hash
that never matched its body (5-plur tris.jsonl seq 5), a hand-written line whose
`sig` is a copy of its hash (6-meridian miles.jsonl seq 21140-21141) -- can not
be removed once genuine events chain on top of it. Until 2026-09-26 such events
were excused by an out-of-band file (`registry/ledger-exceptions.yaml`) that
anyone with commit access could extend. Owner decision 6 (ledger-upgrade PLAN,
2026-09-26): no exceptions. A bad event is cancelled by a `ledger.void` event
INSIDE the ledger, appended by an authorised actor who is not the event's own
writer; history is never rewritten.

THE RECORD. `ledger.void` with payload

    {"log": "miles.jsonl", "seq": 21140, "hash": "<stored hash>",
     "reason": "...", "body_sha256": "<hash the body produces>"}

`log`, `seq` and `hash` name exactly one event as it is stored. `body_sha256`
(written by `ledger_cli.py void`) pins the voided event's body as well: an
event whose stored hash never matched its body can only be pinned that way.
Without `body_sha256` a void applies only while the event still hashes to its
stored hash, so the body is pinned by `hash` itself. Either way, editing the
voided event afterwards makes the void stop applying and verify fail again.

WHO MAY VOID. The void's actor must resolve (principals.yaml, `writes_as`) to a
principal of `kind: human`, or be named in the registry's top-level `voiders:`
list (a principal or writer name). It must NOT be the voided event's writer,
nor another writer of the same principal: no one voids their own events.

A void that fails any of these has no effect, and verify reports it on the
voider's own log, so an attempted self-cancel is itself visible.

A void of a void is allowed (to cancel a mistaken void); a void that is itself
voided by an authorised void has no effect. Resolution is one level deep and
order-free, so two voids naming each other simply cancel nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .events import Event, body_dict, compute_hash, from_line

VOID_TYPE = "ledger.void"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_RUN_SUFFIX = re.compile(r"-run-\d{4}-\d{2}-\d{2}$")

Key = tuple  # (log_stem, seq, stored_hash)


def log_stem(name: str) -> str:
    name = str(name or "")
    return name[:-6] if name.endswith(".jsonl") else name


def _writer(name: str) -> str:
    return _RUN_SUFFIX.sub("", (name or "").strip().lower())


def body_hash(e: Event) -> str:
    return compute_hash(body_dict(e.seq, e.hlc, e.actor, e.type, e.payload, e.prev))


# ── authority ────────────────────────────────────────────────────────────────

_VOIDERS_CACHE: dict = {}


def _registry():
    """(principals, voiders) from the principal registry; empty if unreadable.

    Resolved at call time through `actor_identity.PRINCIPALS`, so a test or an
    operator pointing it elsewhere is honoured. An unreadable or invalid
    registry authorises nobody: a void must never gain force from a broken file.
    """
    import actor_identity
    path = Path(actor_identity.PRINCIPALS)
    try:
        principals = actor_identity.principals(path)
    except ValueError:
        return {}, frozenset()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return principals, frozenset()
    if key not in _VOIDERS_CACHE:
        try:
            import yaml
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            vs = raw.get("voiders") if isinstance(raw, dict) else None
            _VOIDERS_CACHE[key] = frozenset(_writer(v) for v in vs if isinstance(v, str)) \
                if isinstance(vs, list) else frozenset()
        except Exception:  # noqa: BLE001 -- unreadable: no explicit voiders
            _VOIDERS_CACHE[key] = frozenset()
    return principals, _VOIDERS_CACHE[key]


def _principal_of(writer: str, principals: dict) -> tuple[str | None, dict]:
    w = _writer(writer)
    if w in principals and isinstance(principals[w], dict):
        return w, principals[w]
    for name, p in principals.items():
        if isinstance(p, dict) and w in {_writer(x) for x in (p.get("writes_as") or []) if isinstance(x, str)}:
            return name, p
    return None, {}


def refusal(void_actor: str, target_writer: str) -> str | None:
    """Why `void_actor` may not void an event of `target_writer`, or None."""
    principals, voiders = _registry()
    va, tw = _writer(void_actor), _writer(target_writer)
    vp, ventry = _principal_of(va, principals)
    tp, _ = _principal_of(tw, principals)
    if va == tw or (vp is not None and vp == tp):
        return f"{void_actor!r} may not void its own event"
    if vp is None and va not in voiders:
        return f"{void_actor!r} is not a principal in the registry"
    if ventry.get("kind") == "human" or va in voiders or (vp is not None and vp in voiders):
        return None
    return f"{void_actor!r} is not an authorised voider (human principal or `voiders:`)"


# ── resolution ───────────────────────────────────────────────────────────────


def _target(payload) -> tuple[Key, str | None] | str:
    """(target key, body pin) from a void payload, or the reason it is malformed."""
    if not isinstance(payload, dict):
        return "payload is not an object"
    log, seq, h, reason = payload.get("log"), payload.get("seq"), payload.get("hash"), payload.get("reason")
    if not isinstance(log, str) or not log_stem(log):
        return "payload.log missing"
    if type(seq) is not int or seq < 0:
        return "payload.seq must be a non-negative integer"
    if not isinstance(h, str) or not _HEX64.fullmatch(h):
        return "payload.hash must be 64 lowercase hex"
    if not isinstance(reason, str) or not reason.strip():
        return "payload.reason missing"
    pin = payload.get("body_sha256")
    if pin is not None and (not isinstance(pin, str) or not _HEX64.fullmatch(pin)):
        return "payload.body_sha256 must be 64 lowercase hex"
    return (log_stem(log), seq, h), pin


@dataclass
class Voids:
    #: target key -> body pin (None = the stored hash pins the body)
    effective: dict = field(default_factory=dict)
    #: (void log stem, void seq) -> reason it has no effect
    refused: dict = field(default_factory=dict)

    def applies(self, log: str, event: Event, computed: str | None = None) -> bool:
        """Is this event, exactly as it is stored now, cancelled by a void?"""
        key = (log_stem(log), event.seq, event.hash)
        if key not in self.effective:
            return False
        computed = computed if computed is not None else body_hash(event)
        pin = self.effective[key]
        return computed == (pin if pin is not None else event.hash)

    def refusal_for(self, log: str, seq: int) -> str | None:
        return self.refused.get((log_stem(log), seq))

    def __len__(self) -> int:
        return len(self.effective)


def resolve(voids: list[tuple[str, Event]], lookup=None) -> Voids:
    """Decide which void records take effect.

    Args:
        voids: (log stem, event) for every `ledger.void` event in one space.
        lookup: optional `(log_stem, seq) -> Event | None`; when given, a void
            naming no event with that stored hash is refused as such.
    """
    out = Voids()
    authorised: dict[tuple, tuple[Key, str | None]] = {}
    for log, e in voids:
        me = (log_stem(log), e.seq)
        if body_hash(e) != e.hash:
            out.refused[me] = "ledger.void has no effect: its own hash does not match its body"
            continue
        if _writer(e.actor) != _writer(log_stem(log)):
            out.refused[me] = f"ledger.void has no effect: actor {e.actor!r} is not this log's writer"
            continue
        t = _target(e.payload)
        if isinstance(t, str):
            out.refused[me] = f"ledger.void has no effect: {t}"
            continue
        key, pin = t
        why = refusal(e.actor, key[0])
        if why:
            out.refused[me] = f"ledger.void has no effect: {why}"
            continue
        if lookup is not None:
            target = lookup(key[0], key[1])
            if target is None or target.hash != key[2]:
                out.refused[me] = (f"ledger.void has no effect: no event {key[0]}.jsonl seq {key[1]} "
                                   "with that stored hash in this space")
                continue
        authorised[(log_stem(log), e.seq, e.hash)] = (key, pin)
    for me, (key, pin) in authorised.items():
        if me in {k for k, _ in authorised.values()}:
            out.refused[me[:2]] = "ledger.void has no effect: it is itself voided"
            continue
        out.effective[key] = pin
    return out


def from_events(events: list[Event]) -> Voids:
    """Resolve voids over an in-memory, merged event list (fold's input)."""
    vs = [(getattr(e, "log", e.actor), e) for e in events if e.type == VOID_TYPE]
    if not vs:
        return Voids()
    index = {(log_stem(getattr(e, "log", e.actor)), e.seq): e for e in events}
    return resolve(vs, lambda log, seq: index.get((log, seq)))


def _tolerant_events(raw: bytes) -> list[tuple[int, Event]]:
    out = []
    for i, line in enumerate(raw.split(b"\n"), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append((i, from_line(line.decode("utf-8"))))
        except Exception:  # noqa: BLE001 -- a damaged line is verify's to report
            continue
    return out


_SPACE_CACHE: dict = {}


def for_events_dir(events_dir: Path) -> Voids:
    """Resolve every void in one space's `.datacore/events/` directory.

    Reads tolerantly (a damaged line is reported by verify, never here) and
    caches on the stat signature of every log, so verifying each log of a space
    in turn does not re-read the space once per log.
    """
    events_dir = Path(events_dir)
    files = sorted(events_dir.glob("*.jsonl")) if events_dir.is_dir() else []
    try:
        sig = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    except OSError:
        sig = None
    import actor_identity
    try:
        st = Path(actor_identity.PRINCIPALS).stat()
        reg = (str(actor_identity.PRINCIPALS), st.st_mtime_ns, st.st_size)
    except OSError:
        reg = (str(actor_identity.PRINCIPALS),)
    ck = (str(events_dir), sig, reg)
    if sig is not None and ck in _SPACE_CACHE:
        return _SPACE_CACHE[ck]
    raws = {}
    voids: list[tuple[str, Event]] = []
    for p in files:
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        raws[p.stem] = raw
        if b'"ledger.void"' not in raw and b'"ledger.void' not in raw:
            continue
        voids.extend((p.stem, e) for _, e in _tolerant_events(raw) if e.type == VOID_TYPE)
    parsed: dict[str, dict[int, Event]] = {}

    def lookup(log: str, seq: int):
        if log not in raws:
            return None
        if log not in parsed:
            parsed[log] = {e.seq: e for _, e in _tolerant_events(raws[log])}
        return parsed[log].get(seq)

    result = resolve(voids, lookup) if voids else Voids()
    if sig is not None:
        _SPACE_CACHE.clear()          # one space at a time is all any caller needs
        _SPACE_CACHE[ck] = result
    return result


def is_voided(events_dir: Path, log: str, event: Event) -> bool:
    """Is this stored event cancelled by an effective void in its space?"""
    return for_events_dir(events_dir).applies(log, event)
