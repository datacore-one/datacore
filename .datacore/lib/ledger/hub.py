"""The hub: the ledger's front door (DIP-0034, October 2026 amendment, A-E and G).

WHAT IT IS. One process per hub accepts signed appends and lease requests for
the spaces it serves, orders them and answers at once. It holds no state of its
own: every answer it gives is an event in the log (its own per-writer log,
`<grantor>.jsonl`), and everything it knows it reads back from the log. So it
can be stopped, replaced or rebuilt from a git copy of the log and give the same
answers (invariant 5). The ledger stays the arbiter; the hub only decides
FIRST, before anyone has spent anything.

OFF BY DEFAULT. Nothing here runs unless a space opts in with
`<space>/.datacore/ledger-hub.json` (`enable`). A space without it is untouched:
`load_config` returns None, `EventLog` stamps no hub and signs as before, and
`Hub` refuses to start (`hub_off`).

THE PIECES
  * `Hub`        -- accepts appends (`append`), grants and renews leases
                    (`request_lease`, `renew_lease`), records lease conflicts,
                    seals (`seal`), and reports (`status`, `snapshot`).
  * `HubWriter`  -- a writer's side: appends locally first (always safe), then
                    submits; buffers while the hub is unreachable and replays
                    when it returns; asks for leases; and checks its OWN events
                    and leases in the sealed log (`receipt_check`).
  * `LocalTransport` / `HttpTransport` -- in-process and over datacored's
                    `/ledger/hub` API.
  * `enable`, `promote` -- the owner's two commands (`python3 -m ledger.hub`).

RULES THIS MODULE ENFORCES
  * ONE ADMISSION RULE (ledger.hub_admission) for every path an event takes
    into the hub's state: the HTTP append, a git copy read from disk, a rebuild,
    and what the hub seals. Post-cutover every event must be signed by its
    writer with a key this machine holds (no key: not admitted), name this hub
    and carry this space's signing domain (space, hub, log), have a sane clock,
    and chain exactly; leases, renewals, lease conflicts and seals count only
    from an authorised grantor. Legacy (valid unsigned) means membership in the
    owner's cutover manifest (`HubConfig.cutover_logs`), never a timestamp (D; audit
    2026-10-18, findings 1, 2, 6, 7).
  * Every append from a writer is signed by that writer and chains onto what
    the hub already holds for its log; unsigned, wrongly signed, edited, forked,
    gapped, foreign-log, wrong-domain, badly clocked and hub-only appends are
    refused (D). The file lock is held from reading the chain to fsync (16).
  * A fresh request (lease, renewal, seal) is signed with the writer's CURRENT
    key, names this hub and space, is inside the time window and is answered
    once (finding 5, 6). Sealing over the network takes a signed request from
    a seal operator; the API token is transport only (finding 18).
  * A lease is granted only when no live lease exists, only by the ACTIVE hub
    (the grantor the space's config names), only to an eligible actor.
  * No automatic failover (G): a second hub instance refuses to grant (code
    `not_active_hub`) until the owner runs `promote`; a second process of the
    same hub on one host is locked out (`locked`). There is deliberately no
    code path that promotes by itself.
  * After a restart the hub refuses, for one lease period, to re-grant an item
    whose lease expired unrenewed (`restart_grace`): its holder may have
    finished during the outage and not yet replayed.
  * The hub never verifies its own seal. Writers do (`receipt_check`), and any
    other host may (`seal.verify_seal`).

THE STEP-5 SLOT. `eligibility(item, actor, state) -> reason | None` decides
whether `actor` may lease `item`. The default is the assignee rule: an item
addressed to one exact writer is leasable by that writer only; an unaddressed
item and a pool item (`role`) are refused. Roles, pool membership and daily
budgets (amendment F) replace this hook; nothing else in the hub changes.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable

from .events import EVENT_TYPES, Event, canonical_bytes, compute_hash, event_body, from_line, to_line
from .hub_admission import HUB_ONLY_TYPES, Authority, Refusal, admit, admit_chain, check_event, signing_domain
from .log import HubSigningRequired  # noqa: F401 -- re-exported: the hub's refusal to write unsigned

CONFIG = ".datacore/ledger-hub.json"
#: A signed request older or newer than this (hub clock) is refused.
REQUEST_WINDOW_MS = 5 * 60 * 1000
DEFAULT_LEASE_TTL_S = 900
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")

Eligibility = Callable[[object, str, object], "str | None"]


# ── errors ──────────────────────────────────────────────────────────────────

class HubError(Exception):
    def __init__(self, code: str, message: str, **detail):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.detail = code, message, detail

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.detail}


class HubRefused(HubError):
    """The hub answered and said no. Nothing was written."""


class HubUnavailable(HubError):
    """The hub could not be reached. Append locally; lease nothing."""

    def __init__(self, message: str = "the hub is unreachable", **detail):
        super().__init__("unavailable", message, **detail)


# ── the space's opt-in ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class HubConfig:
    hub_id: str
    grantor: str
    lease_ttl_s: int = DEFAULT_LEASE_TTL_S
    #: When the space opted in. Informational ONLY: a timestamp never makes an
    #: event legacy (audit finding 1); the cutover manifest does.
    cutover_hlc: str = ""
    enabled: bool = True
    promoted_by: str = ""
    promoted_at: str = ""
    promote_reason: str = ""
    #: This space's immutable id, inside every event's signing domain (finding 6).
    space_id: str = ""
    #: THE CUTOVER MANIFEST (audit finding 1): {log: {seq, head}} -- each log's
    #: highest seq and head hash when the owner enabled the hub. That history,
    #: and only that, is legacy (valid unsigned). Written once by `enable`, kept
    #: by every later rewrite (`promote`), pinned by `cutover_sha256`.
    cutover_logs: dict = field(default_factory=dict)
    cutover_sha256: str = ""
    #: Grantors this one succeeded (by `promote`): their leases still count.
    previous_grantors: tuple = ()
    #: Who besides the grantor may request a seal over the network (finding 18).
    seal_operators: tuple = ()


def load_config(space_dir) -> HubConfig | None:
    """The space's hub config, or None when the space has not opted in.

    A config that exists but cannot be read raises: a writer must not silently
    fall back to un-hubbed writes in a space that asked for a hub.
    """
    path = Path(space_dir) / CONFIG
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    known = {k: data[k] for k in HubConfig.__dataclass_fields__ if k in data}
    for k in ("previous_grantors", "seal_operators"):
        if k in known:
            if not isinstance(known[k], (list, tuple)) or not all(
                    isinstance(v, str) and _NAME.fullmatch(v) for v in known[k]):
                raise ValueError(f"{path}: {k} must be a list of lowercase names")
            known[k] = tuple(known[k])
    cfg = HubConfig(**known)
    if not (_NAME.fullmatch(cfg.hub_id or "") and _NAME.fullmatch(cfg.grantor or "")):
        raise ValueError(f"{path}: hub_id and grantor must be lowercase names")
    if not _NAME.fullmatch(cfg.space_id or ""):
        raise ValueError(f"{path}: space_id must be a lowercase name (re-run `enable`)")
    if type(cfg.lease_ttl_s) is not int or cfg.lease_ttl_s <= 0:
        raise ValueError(f"{path}: lease_ttl_s must be a positive integer")
    return cfg if cfg.enabled else None


def _write_config(space_dir, cfg: HubConfig) -> None:
    from file_utils import atomic_write_text
    path = Path(space_dir) / CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(asdict(cfg), indent=2, sort_keys=True) + "\n")


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def enable(space_dir, *, hub_id: str, grantor: str, lease_ttl_s: int = DEFAULT_LEASE_TTL_S,
           cutover_hlc: str | None = None, space_id: str | None = None) -> HubConfig:
    """The owner opts a space in. `hub_id` is a stable name, not a hostname (E).

    Also the owner's approval of the cutover: it records the cutover manifest
    (`cutover_logs`), each log's highest seq and head hash as they stand now.
    That history, and only that history, stays valid unsigned (audit finding
    1). Enabling again keeps the manifest and the space id unchanged.
    """
    if not _NAME.fullmatch(hub_id) or not _NAME.fullmatch(grantor):
        raise ValueError("hub_id and grantor must be lowercase names")
    try:
        import actor_identity
        if hub_id == actor_identity.short_hostname():
            raise ValueError("hub_id must be a stable hub name, not this host's name (amendment E)")
    except ImportError:
        pass
    if cutover_hlc is None:
        cutover_hlc = f"{int(time.time() * 1000):013d}"
    try:
        before = json.loads((Path(space_dir) / CONFIG).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        before = {}
    space_id = space_id or before.get("space_id") or secrets.token_hex(8)
    if not _NAME.fullmatch(space_id):
        raise ValueError("space_id must be a lowercase name")
    logs = before.get("cutover_logs") if before.get("cutover_sha256") else None
    if not isinstance(logs, dict):
        logs = _current_heads(space_dir)
    cfg = HubConfig(hub_id=hub_id, grantor=grantor, lease_ttl_s=lease_ttl_s, cutover_hlc=cutover_hlc,
                    space_id=space_id, cutover_logs=logs, cutover_sha256=_manifest_sha(logs))
    _write_config(space_dir, cfg)
    return cfg


def _current_heads(space_dir) -> dict:
    from .log import read_events
    heads: dict = {}
    for e in read_events(Path(space_dir)):
        chain = getattr(e, "log", None) or e.actor
        if type(e.seq) is int and (chain not in heads or e.seq > heads[chain]["seq"]):
            heads[chain] = {"seq": e.seq, "head": e.hash}
    return dict(sorted(heads.items()))


def _manifest_sha(logs: dict) -> str:
    return hashlib.sha256(canonical_bytes({"version": 1, "logs": logs})).hexdigest()


def load_manifest(space_dir, cfg: HubConfig) -> dict:
    """{chain: (highest legacy seq, head hash)} from the cutover manifest.

    Refuses (`bad_manifest`) when it is not the manifest `enable` recorded (its
    sha256 no longer matches): legacy is decided by the owner's manifest or not
    at all. NOT a cryptographic guarantee -- whoever can rewrite the config can
    rewrite both; that needs the manifest signed or recorded in the ledger.
    """
    logs = cfg.cutover_logs
    if not cfg.cutover_sha256 or not isinstance(logs, dict) or _manifest_sha(logs) != cfg.cutover_sha256:
        raise HubRefused("bad_manifest", f"{Path(space_dir) / CONFIG}: the cutover manifest is not the one "
                         "this space was enabled with")
    try:
        return {str(k): (int(v["seq"]), str(v["head"])) for k, v in logs.items()}
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HubRefused("bad_manifest", f"{Path(space_dir) / CONFIG}: the cutover manifest does not parse") from None


def authority(space_dir, cfg: HubConfig, registry_path=None) -> Authority:
    """The explicit input admission and the fold take, read once before replay."""
    return Authority(space_id=cfg.space_id, hub_id=cfg.hub_id,
                     grantors=frozenset({cfg.grantor, *cfg.previous_grantors}),
                     legacy=load_manifest(space_dir, cfg), registry_path=registry_path)


def promote(space_dir, new_grantor: str, *, by: str, reason: str) -> HubConfig:
    """The owner's one command that moves lease granting to another hub (G).

    Explicit by design: nothing in this module calls it. Commit the changed
    config so every copy of the space learns which hub grants.
    """
    cfg = load_config(space_dir)
    if cfg is None:
        raise HubRefused("hub_off", f"{space_dir} has no hub to promote")
    if not _NAME.fullmatch(new_grantor):
        raise ValueError("grantor must be a lowercase name")
    if not by or not reason:
        raise ValueError("a promotion names who ordered it and why")
    previous = tuple(dict.fromkeys((*cfg.previous_grantors, cfg.grantor)))
    previous = tuple(g for g in previous if g != new_grantor)
    cfg = replace(cfg, grantor=new_grantor, promoted_by=by, promoted_at=_iso_now(), promote_reason=reason,
                  previous_grantors=previous)
    _write_config(space_dir, cfg)
    return cfg


# ── signed requests ─────────────────────────────────────────────────────────

def _unsigned(req: dict) -> bytes:
    return canonical_bytes({k: v for k, v in req.items() if k != "sig"})


def sign_request(actor: str, kind: str, body: dict, *, keys_dir=None, clock=None) -> dict:
    """A lease request or renewal, signed with the writer's ledger key."""
    from .keys import sign
    now = (clock or _wall_ms)()
    req = {**body, "kind": kind, "actor": actor, "ts_ms": int(now), "nonce": secrets.token_hex(8)}
    req["sig"] = sign(actor, _unsigned(req), keys_dir=keys_dir)
    return req


def _wall_ms() -> int:
    return int(time.time() * 1000)


# ── the default eligibility: the assignee rule ──────────────────────────────

def assignee_rule(item, actor: str, state) -> str | None:
    """Who may lease `item` until pools exist (amendment F is step 5).

    Same rule as the dispatcher (ledger_claim.py, owner decision L4): only the
    exact writer the item is addressed to. An item addressed to nobody is not
    dispatched; an item carrying `role` waits for pools.
    """
    payload = getattr(item, "payload", None) or {}
    if payload.get("role"):
        return "pool items (role) are not enabled: roles, pools and budgets are amendment F"
    assignee = payload.get("assignee")
    if not assignee:
        return "addressed to nobody: the assignee rule does not dispatch it"
    from actor_identity import dispatch_ambiguity, dispatchable_by
    why = dispatch_ambiguity(str(assignee))
    if why:
        return why
    if not dispatchable_by(actor, str(assignee)):
        return f"addressed to {assignee!r}"
    return None


# ── the hub ─────────────────────────────────────────────────────────────────

def _event_dict(e: Event) -> dict:
    return json.loads(to_line(e))


def _parse_file(path: Path) -> list[Event]:
    from .log import _parse_log_bytes
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return []
    return _parse_log_bytes(raw, path)[0]


def _target(space: Path, log_name: str, event: Event) -> tuple[Path, str]:
    """The file a writer's event lives in, and the chain key readers use for it."""
    from .log import TELEMETRY_SUFFIX, is_telemetry, log_paths
    main, telemetry = log_paths(space, log_name)
    if is_telemetry(event.type, event.payload):
        return telemetry, log_name + TELEMETRY_SUFFIX
    return main, log_name


class Hub:
    """One hub instance serving one space. Thread-safe within a process."""

    def __init__(self, space_dir, actor: str, *, keys_dir=None, registry_path=None, state_dir=None,
                 clock: Callable[[], int] | None = None, eligibility: Eligibility | None = None):
        self.space_dir = Path(space_dir)
        cfg = load_config(self.space_dir)
        if cfg is None:
            raise HubRefused("hub_off", f"{self.space_dir} has not opted in to a hub ({CONFIG})")
        if not _NAME.fullmatch(actor):
            raise ValueError(f"invalid hub actor {actor!r}")
        self.actor = actor
        self.keys_dir, self.registry_path = keys_dir, registry_path
        load_manifest(self.space_dir, cfg)          # a hub over an edited manifest does not start
        self.clock = clock or _wall_ms
        self.eligibility: Eligibility = eligibility or assignee_rule
        self.started_ms = self.clock()
        self.closed = False
        self._mutex = threading.RLock()
        self._nonces: dict[str, int] = {}
        from .log import EventLog
        self.log = EventLog(self.space_dir, actor, keys_dir=keys_dir, registry_path=registry_path,
                            sign=True, hub=cfg.hub_id)
        # ONE PROCESS PER HUB PER HOST. A second instance of the same hub would
        # write the same log from two caches.
        state = Path(state_dir) if state_dir else Path.home() / ".datacore" / "state" / "hub"
        state.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(str(self.space_dir.resolve()).encode()).hexdigest()[:16]
        self._lock_path = state / f"{key}.lock"
        fh = open(self._lock_path, "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fh = fh
        except OSError:
            fh.close()
            self._lock_fh = None

    # ── housekeeping ───────────────────────────────────────────────────────
    def close(self) -> None:
        with self._mutex:
            self.closed = True
            if self._lock_fh is not None:
                try:
                    fcntl.flock(self._lock_fh, fcntl.LOCK_UN)
                finally:
                    self._lock_fh.close()
                    self._lock_fh = None

    def config(self) -> HubConfig:
        cfg = load_config(self.space_dir)
        if cfg is None:
            raise HubRefused("hub_off", f"{self.space_dir} no longer opts in to a hub")
        return cfg

    def _writable(self, *, grant: bool) -> HubConfig:
        if self.closed:
            raise HubUnavailable("this hub instance is closed")
        if self._lock_fh is None:
            raise HubRefused("locked", "another instance of this hub holds the lock on this host")
        cfg = self.config()
        if grant and cfg.grantor != self.actor:
            raise HubRefused("not_active_hub", f"this hub ({self.actor}) does not grant for this space; "
                             f"{cfg.grantor} does. Moving that is the owner's `promote` command, never automatic",
                             active=cfg.grantor)
        return cfg

    def _authority(self, cfg: HubConfig) -> Authority:
        return authority(self.space_dir, cfg, self.registry_path)

    def _state(self):
        """The admitted events and their fold: every reader of this hub's state
        (answers, status, snapshot, seal) goes through the one admission rule,
        whether an event came over HTTP or by git (audit finding 2)."""
        from .fold import fold
        from .log import read_events
        auth = self._authority(self.config())
        events = admit(read_events(self.space_dir), auth, now_ms=self.clock()).events
        return events, fold(events, lease_grantors=auth.lease_grantors())

    def refusals(self) -> dict:
        """{log: where and why it stops being admitted} -- for status and verify."""
        from .log import read_events
        return admit(read_events(self.space_dir), self._authority(self.config()), now_ms=self.clock()).refused

    # ── appends (first confirmation) ───────────────────────────────────────
    def append(self, submission: dict) -> dict:
        """Accept one signed writer event: verify, chain, store, answer."""
        raw = submission.get("event", submission) if isinstance(submission, dict) else None
        if not isinstance(raw, dict):
            raise HubRefused("malformed", "expected {event: {...}, log?: name}")
        try:
            event = Event(**raw)
        except TypeError as exc:
            raise HubRefused("malformed", f"not an event: {exc}") from None
        log_name = (submission.get("log") if isinstance(submission, dict) else None) or event.actor
        with self._mutex:
            cfg = self._writable(grant=False)
            self._check_submission(event, log_name, cfg)
            if not event.sig:
                # Network admission never admits legacy: unsigned history comes by
                # git and counts only as the cutover manifest names it (finding 1).
                raise HubRefused("unsigned", "the hub accepts only signed events (amendment D)")
            auth = self._authority(cfg)
            path, chain = _target(self.space_dir, log_name, event)
            from file_utils import fsync_directory
            from .log import CorruptLogError, _parse_log_bytes
            if not path.exists() and self._onto(event, chain, [], cfg, auth) is not None:
                return self._receipt("duplicate", cfg, chain, event)   # unreachable: an empty chain
            # (A refusal onto a log the hub does not hold yet creates no file.)
            path.parent.mkdir(parents=True, exist_ok=True)
            # ONE LOCK from reading the chain to fsync (audit finding 16): the
            # same flock every EventLog writer of this file takes, so a local
            # append or an import cannot land between the check and the write.
            with open(path, "a+b") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.seek(0)
                    raw = f.read()
                    if raw and not raw.endswith(b"\n"):
                        raise HubRefused("bad_chain", f"{path.name} ends in a torn line; not appending")
                    try:
                        existing = _parse_log_bytes(raw, path)[0]
                    except CorruptLogError as exc:
                        raise HubRefused("bad_chain", str(exc)) from None
                    if self._onto(event, chain, existing, cfg, auth) is not None:
                        return self._receipt("duplicate", cfg, chain, event)
                    f.write((to_line(event) + "\n").encode("utf-8"))
                    f.flush()
                    os.fsync(f.fileno())
                    fsync_directory(path.parent)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            receipt = self._receipt("accepted", cfg, chain, event)
            self._record_conflicts(event)
            return receipt

    def _onto(self, event: Event, chain: str, existing: list, cfg: HubConfig, auth: Authority) -> str | None:
        """May `event` be appended onto `existing` (the chain as the file holds it)?
        "duplicate" when it is already there; raises HubRefused otherwise."""
        by_seq = {e.seq: e for e in existing}
        if type(event.seq) is int and event.seq in by_seq:
            if by_seq[event.seq].hash == event.hash:
                return "duplicate"
            raise HubRefused("fork", f"{chain} seq {event.seq} is already a different event")
        expected = existing[-1].seq + 1 if existing else 0
        if event.seq != expected:
            raise HubRefused("gap", f"{chain} expects seq {expected}; replay from there", expected=expected)
        now = self.clock()
        _, why = admit_chain(chain, existing, auth, now_ms=now)
        if why:
            raise HubRefused("chain_refused", f"{chain} is not admissible from {why}; "
                             "nothing more is accepted onto it")
        try:
            check_event(event, chain, existing[-1] if existing else None, auth, now_ms=now, current_key=True)
        except Refusal as exc:
            raise HubRefused(exc.code, exc.message) from None
        return None

    def _check_submission(self, event: Event, log_name: str, cfg: HubConfig) -> None:
        if not isinstance(log_name, str) or not _NAME.fullmatch(log_name):
            raise HubRefused("malformed", f"invalid log name {log_name!r}")
        if event.type not in EVENT_TYPES:
            raise HubRefused("unknown_type", f"unknown event type {event.type!r}")
        if event.type in HUB_ONLY_TYPES:
            raise HubRefused("hub_only_type", f"{event.type} is written only by the hub")
        from actor_identity import base_writer
        if not isinstance(event.actor, str) or base_writer(log_name) != base_writer(event.actor):
            raise HubRefused("foreign_log", f"{event.actor!r} may append only to its own log, not {log_name!r}")
        if base_writer(event.actor) in {self.actor, cfg.grantor, *cfg.previous_grantors}:
            raise HubRefused("hub_log", "a hub's own log is written only by that hub")
        # Everything else -- signature, hub, domain, clock, chain -- is the one
        # admission rule (ledger.hub_admission.check_event), applied in `append`.

    def _receipt(self, status: str, cfg: HubConfig, chain: str, event: Event) -> dict:
        return {"status": status, "hub": cfg.hub_id, "log": chain, "seq": event.seq, "hash": event.hash}

    def _record_conflicts(self, event: Event) -> None:
        """Make a conflict the fold found durable: one `item.lease.conflict` each."""
        item_id = (event.payload or {}).get("id") if isinstance(event.payload, dict) else None
        if not isinstance(item_id, str):
            return
        try:
            cfg = self._writable(grant=True)
        except HubError:
            return                      # a standby records nothing of its own
        events, state = self._state()
        item = state.items.get(item_id)
        if item is None or not item.lease_conflicts:
            return
        floor = _item_floor(events, item_id)
        recorded = {(e.payload.get("event") or {}).get("hash") for e in events
                    if e.type == "item.lease.conflict" and e.actor == cfg.grantor
                    and isinstance(e.payload.get("event"), dict)}
        by_hash = {e.hash: e for e in events}
        for h, why in sorted(item.lease_conflicts.items()):
            if h in recorded or h not in by_hash:
                continue
            bad = by_hash[h]
            floor = self.log.append("item.lease.conflict", {
                "id": item_id, "reason": why,
                "event": {"log": getattr(bad, "log", None) or bad.actor, "seq": bad.seq, "hash": h,
                          "actor": bad.actor, "type": bad.type}}, after=floor).hlc

    # ── leases ─────────────────────────────────────────────────────────────
    def _verify_request(self, req: dict, kind: str, cfg: HubConfig) -> str:
        from .keys import verify
        if not isinstance(req, dict) or req.get("kind") != kind:
            raise HubRefused("malformed", f"expected a signed {kind}")
        actor, sig, ts, nonce = req.get("actor"), req.get("sig"), req.get("ts_ms"), req.get("nonce")
        if not (isinstance(actor, str) and _NAME.fullmatch(actor) and isinstance(sig, str)
                and type(ts) is int and isinstance(nonce, str) and nonce):
            raise HubRefused("malformed", f"a {kind} carries actor, ts_ms, nonce and sig")
        now = self.clock()
        # A FRESH request is judged by the key the writer holds NOW (audit
        # finding 5): a key rotated out stays valid for the history it signed,
        # never for new requests.
        if not verify(actor, _unsigned(req), sig, registry_path=self.registry_path, at_ms=now):
            raise HubRefused("bad_signature", f"the {kind} is not signed with {actor!r}'s current key")
        if abs(now - ts) > REQUEST_WINDOW_MS:
            raise HubRefused("stale_request", f"the {kind} is {abs(now - ts) // 1000}s from the hub's clock")
        if req.get("hub") != cfg.hub_id:
            raise HubRefused("wrong_hub", f"this is hub {cfg.hub_id!r}, not {req.get('hub')!r}")
        if req.get("space") != cfg.space_id:
            raise HubRefused("wrong_space", f"this is space {cfg.space_id!r}, not {req.get('space')!r}")
        for n, t in list(self._nonces.items()):
            if abs(now - t) > REQUEST_WINDOW_MS:
                del self._nonces[n]
        if nonce in self._nonces:
            raise HubRefused("replayed_request", "this request was already answered")
        self._nonces[nonce] = ts
        return actor

    def request_lease(self, req: dict) -> dict:
        """Grant `req["item"]` to the signing actor if no live lease exists."""
        from .fold import lease_live
        with self._mutex:
            cfg = self._writable(grant=True)
            actor = self._verify_request(req, "lease.request", cfg)
            item_id = req.get("item")
            events, state = self._state()
            item = state.items.get(item_id) if isinstance(item_id, str) else None
            if item is None:
                raise HubRefused("unknown_item", f"no item {item_id!r} in this space")
            now = self.clock()
            if item.status in ("completed", "verified", "dismissed", "archived"):
                raise HubRefused("closed", f"{item_id} is {item.status}")
            if lease_live(item, now):
                if item.lease_holder == actor:
                    return {"status": "held", "lease": dict(item.lease_ref or {}),
                            "expires_ms": item.lease_expires_ms}
                raise HubRefused("taken", f"{item_id} is leased to {item.lease_holder}",
                                 holder=item.lease_holder, expires_ms=item.lease_expires_ms)
            if item.lease_holder is not None and now < self.started_ms + cfg.lease_ttl_s * 1000:
                raise HubRefused("restart_grace", f"{item_id}'s lease to {item.lease_holder} expired while "
                                 "the hub was away; its holder may still replay a result",
                                 holder=item.lease_holder, retry_after_ms=self.started_ms + cfg.lease_ttl_s * 1000)
            if item.status != "created" and item.lease_holder is None:
                raise HubRefused("claimed", f"{item_id} is {item.status} outside a lease (owner {item.owner})")
            if item.edit_conflicts:
                raise HubRefused("conflicted", f"{item_id} has unresolved edit conflicts")
            why = self.eligibility(item, actor, state)
            if why:
                raise HubRefused("not_eligible", why)
            # The answer sorts after every event of its item (audit finding 7),
            # whatever the sibling logs' tails say.
            event = self.log.append("item.lease", {
                "id": item_id, "holder": actor, "granted_ms": now,
                "expires_ms": now + cfg.lease_ttl_s * 1000,
                "request": _request_ref(req)}, after=_item_floor(events, item_id))
            return {"status": "granted", "lease": _event_dict(event), "log": self.log.log_name}

    def renew_lease(self, req: dict) -> dict:
        with self._mutex:
            cfg = self._writable(grant=True)
            actor = self._verify_request(req, "lease.renew", cfg)
            item_id = req.get("item")
            events, state = self._state()
            item = state.items.get(item_id) if isinstance(item_id, str) else None
            if item is None or item.lease_holder != actor or item.status != "claimed":
                raise HubRefused("not_holder", f"{actor} holds no lease on {item_id!r}")
            now = self.clock()
            event = self.log.append("item.lease.renew", {
                "id": item_id, "holder": actor, "renewed_ms": now, "expires_ms": now + cfg.lease_ttl_s * 1000,
                "lease": {k: (item.lease_ref or {}).get(k) for k in ("log", "seq", "hash")},
                "request": _request_ref(req)}, after=_item_floor(events, item_id))
            return {"status": "renewed", "lease": _event_dict(event), "log": self.log.log_name}

    # ── the second confirmation's raw material ─────────────────────────────
    def seal(self) -> dict:
        """Seal the space as it stands (seal.py's shape), over ADMITTED events
        only. Publishing it is git's job (commit the events directory on the
        hub's schedule). The hub never checks its own seal.

        In-process callers (the hub's own scheduler) call this directly. Over
        the network a seal is `seal_request`, signed by an operator. Idempotent:
        when nothing changed since this hub's last seal, nothing is written
        (`unchanged`), so repeated calls cannot grow the log (finding 18).
        """
        from .seal import build_seal_payload
        with self._mutex:
            self._writable(grant=True)
            events, _ = self._state()
            try:
                payload = build_seal_payload(events)
            except ValueError as exc:
                raise HubRefused("unsealable", str(exc)) from None
            last = next((e for e in reversed(events) if e.type == "ledger.seal" and e.actor == self.actor), None)
            if last is not None and last.payload == payload:
                return {"status": "unchanged", "seal": _event_dict(last), "log": self.log.log_name}
            event = self.log.append("ledger.seal", payload)
            return {"status": "sealed", "seal": _event_dict(event), "log": self.log.log_name}

    def seal_request(self, req: dict | None) -> dict:
        """A seal asked for over the network: only by a signed request from the
        grantor (its scheduler) or a configured seal operator. The API's bearer
        token is transport; it never makes the hub sign anything (finding 18)."""
        if not isinstance(req, dict) or not req.get("sig"):
            raise HubRefused("unsigned", "a seal is asked for by a signed seal.request; "
                             "the API token is transport only")
        with self._mutex:
            cfg = self._writable(grant=True)
            actor = self._verify_request(req, "seal.request", cfg)
            if actor not in {cfg.grantor, *cfg.seal_operators}:
                raise HubRefused("not_operator", f"{actor!r} is not a seal operator of this space")
            return self.seal()

    # ── reports ────────────────────────────────────────────────────────────
    def status(self) -> dict:
        from .fold import lease_live
        from .seal import watermarks
        cfg = self.config()
        events, state = self._state()
        now = self.clock()
        return {
            "hub": cfg.hub_id, "actor": self.actor, "grantor": cfg.grantor,
            "active": cfg.grantor == self.actor and self._lock_fh is not None and not self.closed,
            "started_ms": self.started_ms, "now_ms": now,
            "watermarks": watermarks(events),
            "leases": {iid: {"holder": it.lease_holder, "expires_ms": it.lease_expires_ms,
                             "live": lease_live(it, now)}
                       for iid, it in sorted(state.items.items()) if it.lease_holder is not None},
        }

    def snapshot(self) -> dict:
        """Everything the hub's answers depend on, and nothing process-local.
        A hub rebuilt from a copy of the log returns an equal snapshot."""
        from .seal import watermarks
        cfg = self.config()
        events, state = self._state()
        return {
            "hub": cfg.hub_id, "grantor": cfg.grantor, "state_root": state.state_root(),
            "watermarks": watermarks(events),
            "leases": {iid: {"holder": it.lease_holder, "expires_ms": it.lease_expires_ms,
                             "ref": it.lease_ref}
                       for iid, it in sorted(state.items.items()) if it.lease_ref is not None},
            "conflicts": {iid: sorted(it.lease_conflicts) for iid, it in sorted(state.items.items())
                          if it.lease_conflicts},
        }


def _request_ref(req: dict) -> dict:
    """What a hub answer records of the request it answers (bound by the writer, finding 5)."""
    return {"kind": req["kind"], "nonce": req["nonce"], "ts_ms": req["ts_ms"]}


def _item_floor(events, item_id: str) -> str | None:
    """The latest HLC of any event about `item_id`: an answer about it sorts after."""
    return max((e.hlc for e in events if isinstance(e.payload, dict) and e.payload.get("id") == item_id
                and isinstance(e.hlc, str)), default=None)


# ── transports ──────────────────────────────────────────────────────────────

class LocalTransport:
    """In-process transport. `down = True` simulates an unreachable hub."""

    def __init__(self, hub: Hub):
        self.hub, self.down = hub, False

    def _hub(self) -> Hub:
        if self.down or self.hub is None or self.hub.closed:
            raise HubUnavailable()
        return self.hub

    def append(self, submission: dict) -> dict:
        return self._hub().append(submission)

    def request_lease(self, req: dict) -> dict:
        return self._hub().request_lease(req)

    def renew_lease(self, req: dict) -> dict:
        return self._hub().renew_lease(req)

    def status(self) -> dict:
        return self._hub().status()

    def seal_request(self, req: dict) -> dict:
        return self._hub().seal_request(req)


class HttpTransport:
    """datacored's `/ledger/hub` API (stdlib urllib). Not exercised against a
    running daemon in this repository's tests; the daemon's own tests cover the
    routes in-process."""

    def __init__(self, base_url: str, token: str, space: str, *, timeout_s: float = 10.0):
        self.base, self.token, self.timeout = base_url.rstrip("/"), token, timeout_s
        self.prefix = f"/ledger/hub/{space}"

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        import urllib.error
        import urllib.request
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}").get("detail") or {}
            except ValueError:
                detail = {}
            if exc.code == 503 or not isinstance(detail, dict) or "code" not in detail:
                raise HubUnavailable(f"hub answered HTTP {exc.code}") from None
            raise HubRefused(detail["code"], detail.get("message", ""),
                             **{k: v for k, v in detail.items() if k not in ("code", "message")}) from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise HubUnavailable(f"hub unreachable: {exc.__class__.__name__}") from None

    def append(self, submission: dict) -> dict:
        return self._call("POST", self.prefix + "/append", submission)

    def request_lease(self, req: dict) -> dict:
        return self._call("POST", self.prefix + "/lease", req)

    def renew_lease(self, req: dict) -> dict:
        return self._call("POST", self.prefix + "/lease/renew", req)

    def status(self) -> dict:
        return self._call("GET", self.prefix + "/status")

    def seal_request(self, req: dict) -> dict:
        return self._call("POST", self.prefix + "/seal", req)


# ── the writer's side ───────────────────────────────────────────────────────

class HubWriter:
    """A writer in a hub space: local first, hub second, receipt third.

    Its own per-writer log IS the buffer: while the hub is unreachable every
    append still lands locally (safe without coordination), and `replay` sends
    whatever the hub does not yet hold, in order. Pool leasing pauses: a lease
    needs the hub, and `request_lease` raises `HubUnavailable`.
    """

    def __init__(self, space_dir, actor: str, transport, *, keys_dir=None, registry_path=None,
                 state_dir=None, clock: Callable[[], int] | None = None, log_name: str | None = None):
        self.space_dir = Path(space_dir)
        cfg = load_config(self.space_dir)
        if cfg is None:
            raise HubRefused("hub_off", f"{self.space_dir} has not opted in to a hub ({CONFIG})")
        self.cfg, self.actor, self.transport = cfg, actor, transport
        self.keys_dir, self.registry_path = keys_dir, registry_path
        self.clock = clock or _wall_ms
        from .log import EventLog
        self.log = EventLog(self.space_dir, actor, keys_dir=keys_dir, registry_path=registry_path,
                            sign=True, log_name=log_name)
        self.state_dir = Path(state_dir) if state_dir else Path.home() / ".datacore" / "state" / "hub-writer" / actor
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # ── local state: receipts, floor, outage ───────────────────────────────
    @property
    def _receipts_path(self) -> Path:
        """Per space, hub, log and actor (audit finding 6): one actor writing in
        two spaces keeps two sets of lease receipts and two causal floors."""
        d = self.state_dir / f"{self.cfg.space_id}.{self.cfg.hub_id}.{self.log.log_name}"
        d.mkdir(parents=True, exist_ok=True)
        return d / "lease-receipts.jsonl"

    def receipts(self) -> list[dict]:
        try:
            return [json.loads(ln) for ln in self._receipts_path.read_text().splitlines() if ln.strip()]
        except FileNotFoundError:
            return []

    def _floor(self) -> str | None:
        hlcs = [r["event"]["hlc"] for r in self.receipts()]
        return max(hlcs) if hlcs else None

    def outage(self) -> dict | None:
        """{since_ms} while the hub has been unreachable, for the briefing (G)."""
        try:
            return json.loads((self.state_dir / "outage.json").read_text())
        except FileNotFoundError:
            return None

    def _mark_outage(self) -> None:
        if self.outage() is None:
            from file_utils import atomic_write_text
            atomic_write_text(self.state_dir / "outage.json", json.dumps({"since_ms": self.clock()}))

    def _clear_outage(self) -> None:
        try:
            (self.state_dir / "outage.json").unlink()
        except FileNotFoundError:
            pass

    # ── appends ────────────────────────────────────────────────────────────
    def _submission(self, e: Event) -> dict:
        return {"event": _event_dict(e), "log": self.log.log_name}

    def append(self, type: str, payload: dict) -> tuple[Event, dict | None]:
        """Append locally, then submit. Returns (event, receipt or None if buffered)."""
        event = self.log.append(type, payload, after=self._floor())
        try:
            receipt = self.transport.append(self._submission(event))
        except HubUnavailable:
            self._mark_outage()
            return event, None
        except HubRefused as exc:
            if exc.code != "gap":
                raise
            # Earlier events are still buffered: send them first, in order.
            receipts = self.replay()
            receipt = next((r for r in receipts if r.get("hash") == event.hash), None)
            return event, receipt
        self._clear_outage()
        return event, receipt

    def _local_chains(self) -> list[tuple[str, list[Event]]]:
        from .log import TELEMETRY_SUFFIX
        name = self.log.log_name
        return [(name, _parse_file(self.log.path)), (name + TELEMETRY_SUFFIX, _parse_file(self.log.telemetry_path))]

    def replay(self) -> list[dict]:
        """Send every local event the hub does not yet hold, oldest first."""
        try:
            held = self.transport.status().get("watermarks", {})
        except HubUnavailable:
            self._mark_outage()
            raise
        out = []
        for chain, events in self._local_chains():
            mark = held.get(chain, -1)
            for e in events:
                if e.seq > mark:
                    out.append(self.transport.append(self._submission(e)))
        self._clear_outage()
        return out

    # ── leases ─────────────────────────────────────────────────────────────
    def _ask(self, method: str, kind: str, item_id: str) -> dict:
        req = sign_request(self.actor, kind, {"hub": self.cfg.hub_id, "space": self.cfg.space_id,
                                              "item": item_id},
                           keys_dir=self.keys_dir, clock=self.clock)
        try:
            answer = getattr(self.transport, method)(req)
        except HubUnavailable:
            self._mark_outage()
            raise
        if answer.get("status") in ("granted", "renewed"):
            expected = "item.lease" if kind == "lease.request" else "item.lease.renew"
            self._accept_lease(answer.get("lease"), item_id, req, expected)
        return answer

    def request_lease(self, item_id: str) -> dict:
        return self._ask("request_lease", "lease.request", item_id)

    def renew_lease(self, item_id: str) -> dict:
        return self._ask("renew_lease", "lease.renew", item_id)

    def _accept_lease(self, lease, item_id: str, req: dict, expected_type: str) -> None:
        """First confirmation: check the hub's signed answer before relying on it.

        The answer must be THE answer to THIS request (audit finding 5): an
        `expected_type` event, signed with the current key of the grantor this
        writer's copy of the space names, in that grantor's own log of this
        space and hub, granting `item_id` to this writer, and recording this
        request's nonce. Any registered writer's signature is not enough, and an
        earlier genuine answer replayed for a new request is refused.
        """
        from .keys import verify
        cfg = load_config(self.space_dir) or self.cfg
        try:
            e = Event(**lease)
        except TypeError:
            raise HubRefused("bad_lease", "the hub's lease answer is not an event; not starting") from None
        payload = e.payload if isinstance(e.payload, dict) else {}
        ref = payload.get("request") if isinstance(payload.get("request"), dict) else {}
        checks = [
            (e.type == expected_type, f"answer is a {e.type!r}, not a {expected_type!r}"),
            (e.actor == cfg.grantor, f"answer is signed by {e.actor!r}, not this space's grantor {cfg.grantor!r}"),
            (e.hub == cfg.hub_id and e.domain == signing_domain(cfg.space_id, cfg.hub_id, e.actor),
             "answer belongs to another space, hub or log"),
            (payload.get("id") == item_id and payload.get("holder") == self.actor,
             f"answer is not a lease on {item_id} to {self.actor}"),
            (ref.get("nonce") == req["nonce"] and ref.get("kind") == req["kind"],
             "answer does not answer this request (nonce)"),
            (compute_hash(event_body(e)) == e.hash, "answer's hash does not match its body"),
        ]
        for ok, why in checks:
            if not ok:
                raise HubRefused("bad_lease", f"the hub's lease answer does not verify ({why}); not starting")
        if not verify(e.actor, canonical_bytes(event_body(e)), e.sig, registry_path=self.registry_path,
                      at_ms=self.clock()):
            raise HubRefused("bad_lease", "the hub's lease answer is not signed with the grantor's current key; "
                             "not starting")
        with open(self._receipts_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"item": item_id, "log": e.actor, "space": cfg.space_id, "hub": cfg.hub_id,
                                "event": lease}, sort_keys=True) + "\n")

    # ── second confirmation ────────────────────────────────────────────────
    def receipt_check(self, sealed_space, *, hub_actor: str | None = None, required=None) -> dict:
        """Second confirmation: are MY events and MY leases in the sealed log exactly as signed?

        `sealed_space`: a checkout of the space as published to git.
        `required`: hashes of the events one operation needs settled (its lease,
        its result, ...); default every event this writer wrote and every lease
        it was granted.

        Returns {status, ok, problems, pending} (audit finding 3):
          * "confirmed" -- the seal verifies (`verify_seal` is True) and every
            required event is covered by it, byte for byte as signed;
          * "pending"   -- nothing is wrong yet, but something required is not
            covered, there is no seal yet, or the seal cannot be judged here;
          * "failed"    -- something differs, is missing or does not verify.
        `ok` is True ONLY for "confirmed": pending is never permission.

        Independent of the hub by construction: it compares the sealed copy with
        this writer's own log and its own record of the hub's answers. Not
        verified here: that `sealed_space` is the published commit (the caller
        passes the checkout it trusts).
        """
        from .keys import verify
        from .log import read_events
        from .seal import as_sequencer, latest_seal, verify_seal
        from .verify import check_events
        hub_actor = hub_actor or (load_config(self.space_dir) or self.cfg).grantor
        events = read_events(Path(sealed_space))
        problems: list[str] = []
        pending: list[str] = []
        covered: set[str] = set()

        def result(status: str) -> dict:
            return {"status": status, "ok": status == "confirmed", "problems": problems, "pending": pending}

        with as_sequencer(hub_actor):
            try:
                seal = latest_seal(events)
            except ValueError as exc:
                problems.append(f"unreadable seal: {exc}")
                return result("failed")
            verdict, detail = verify_seal(events) if seal is not None else (None, "no seal yet")
        if seal is None:
            pending.append(f"no seal by {hub_actor} yet")
        elif verdict is False:
            problems.append(f"the seal does not verify: {detail}")
        elif verdict is None:
            pending.append(f"the seal cannot be verified here yet: {detail}")
        marks = seal.watermarks if seal is not None else {}
        sealed = {}
        for e in events:
            sealed.setdefault(getattr(e, "log", None) or e.actor, {})[e.seq] = e
        # The hub's seal itself must be the hub's, in an intact hub chain.
        hub_chain = sorted(sealed.get(hub_actor, {}).values(), key=lambda e: e.seq)
        for p in check_events(list(enumerate(hub_chain, 1)), registry_path=self.registry_path, strict=True):
            if p.kind != "clock":
                problems.append(f"hub log {hub_actor}: {p.text}")
        # My events, line for line, as I signed them. A different event at my
        # seq is a problem whether or not a seal covers it yet.
        mine_all: list[str] = []
        for chain, mine in self._local_chains():
            copy = sealed.get(chain, {})
            for e in mine:
                mine_all.append(e.hash)
                s = copy.get(e.seq)
                if s is not None and to_line(s) != to_line(e):
                    problems.append(f"{chain} seq {e.seq}: differs from what I signed")
                elif e.seq > marks.get(chain, -1):
                    pending.append(f"{chain} seq {e.seq} not sealed yet")
                elif s is None:
                    problems.append(f"{chain} seq {e.seq}: missing from the sealed log")
                elif not verify(self.actor, canonical_bytes(event_body(s)), s.sig, registry_path=self.registry_path,
                                at_ms=_physical_ms(s.hlc)):
                    problems.append(f"{chain} seq {e.seq}: my signature does not verify in the sealed log")
                else:
                    covered.add(e.hash)
        # The leases the hub granted me, as it signed them at the first confirmation.
        for r in self.receipts():
            lease = Event(**r["event"])
            mine_all.append(lease.hash)
            s = sealed.get(r["log"], {}).get(lease.seq)
            if s is not None and (s.hash != lease.hash or to_line(s) != to_line(lease)):
                problems.append(f"lease on {r['item']} (hub seq {lease.seq}) does not match the sealed log")
            elif lease.seq > marks.get(r["log"], -1):
                pending.append(f"lease on {r['item']} (hub seq {lease.seq}) not sealed yet")
            elif s is None:
                problems.append(f"lease on {r['item']} (hub seq {lease.seq}) is missing from the sealed log")
            else:
                covered.add(lease.hash)
        if problems:
            return result("failed")
        needed = set(mine_all) if required is None else set(required)
        missing = sorted(needed - covered)
        if required is not None:
            pending.extend(f"required event {h[:12]} is not covered" for h in missing)
        if seal is None or verdict is not True or missing:
            return result("pending")
        return result("confirmed")


def _physical_ms(hlc) -> int | None:
    try:
        return int(str(hlc).split(".", 1)[0])
    except ValueError:
        return None


def strict_problems(space_dir, *, registry_path=None, now_ms: int | None = None) -> list[str]:
    """Where each log of a hub space stops being admissible (amendment D).

    The one admission rule (ledger.hub_admission): after the cutover manifest's
    heads, every event is signed, domain-bound and chained. Not by timestamp
    (audit finding 1). Empty for a space without a hub.
    """
    from .log import read_events
    cfg = load_config(space_dir)
    if cfg is None:
        return []
    auth = authority(space_dir, cfg, registry_path)
    refused = admit(read_events(Path(space_dir)), auth, now_ms=_wall_ms() if now_ms is None else now_ms).refused
    return [f"{chain} {why}" for chain, why in sorted(refused.items())]


# ── the owner's commands ────────────────────────────────────────────────────

def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python3 -m ledger.hub", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    en = sub.add_parser("enable", help="opt a space in to a hub (owner)")
    en.add_argument("space")
    en.add_argument("--hub-id", required=True)
    en.add_argument("--grantor", required=True)
    en.add_argument("--lease-ttl-s", type=int, default=DEFAULT_LEASE_TTL_S)
    pr = sub.add_parser("promote", help="move lease granting to another hub (owner; never automatic)")
    pr.add_argument("space")
    pr.add_argument("grantor")
    pr.add_argument("--by", required=True)
    pr.add_argument("--reason", required=True)
    sh = sub.add_parser("show", help="print a space's hub config")
    sh.add_argument("space")
    a = ap.parse_args(argv)
    if a.cmd == "enable":
        cfg = enable(a.space, hub_id=a.hub_id, grantor=a.grantor, lease_ttl_s=a.lease_ttl_s)
    elif a.cmd == "promote":
        cfg = promote(a.space, a.grantor, by=a.by, reason=a.reason)
    else:
        cfg = load_config(a.space)
    print(json.dumps(asdict(cfg) if cfg else {"enabled": False}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
