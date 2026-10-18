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
  * Every append from a writer is signed by that writer and chains onto what
    the hub already holds for its log; unsigned, wrongly signed, edited, forked,
    gapped, foreign-log and hub-only appends are refused (D).
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

ELIGIBILITY. `eligibility(item, actor, state) -> reason | None` decides
whether `actor` may lease `item`. The default is the assignee rule: an item
addressed to one exact writer is leasable by that writer only; an unaddressed
item and a pool item (`role`) are refused. A space that opts in to pools
(`enable_pools`, amendment F) uses `pool_rule` instead. The role is named in
the task (owner decision 2026-10-18): an addressed task keeps the assignee
rule; an unaddressed task with a role and a DONE_WHEN may be leased by any
registered agent principal, while the role's daily `pool.budget` lasts. Either
way a role in the task makes the grant name the role description to load
(`.datacore/templates/roles/<role>.md`), and a role with none is refused.
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
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

from .events import (EVENT_TYPES, HUB_EVENT_TYPES, POOL_EVENT_TYPES, Event, canonical_bytes, event_body,
                     from_line, to_line)
from .log import HubSigningRequired  # noqa: F401 -- re-exported: the hub's refusal to write unsigned

CONFIG = ".datacore/ledger-hub.json"
#: Types only a hub writes. A writer submitting one is refused.
HUB_ONLY_TYPES = HUB_EVENT_TYPES | POOL_EVENT_TYPES | {"ledger.seal"}
#: Role descriptions (amendment F): a task's role must have one here. The grant
#: names it by its installation-relative path, ROLES_REL/<file>.
ROLES_DIR = Path(__file__).resolve().parents[2] / "templates" / "roles"
ROLES_REL = ".datacore/templates/roles"
_ROLE = re.compile(r"[a-z][a-z0-9_-]*(\.[a-z0-9_-]+)*")
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
    #: Events stamped before this HLC are legacy: valid unsigned (amendment D).
    cutover_hlc: str = ""
    enabled: bool = True
    promoted_by: str = ""
    promoted_at: str = ""
    promote_reason: str = ""
    #: Role pools (amendment F). Off unless the owner runs `enable_pools`.
    pools: bool = False


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
    cfg = HubConfig(**known)
    if not (_NAME.fullmatch(cfg.hub_id or "") and _NAME.fullmatch(cfg.grantor or "")):
        raise ValueError(f"{path}: hub_id and grantor must be lowercase names")
    if type(cfg.lease_ttl_s) is not int or cfg.lease_ttl_s <= 0:
        raise ValueError(f"{path}: lease_ttl_s must be a positive integer")
    if type(cfg.pools) is not bool:
        raise ValueError(f"{path}: pools must be true or false")
    return cfg if cfg.enabled else None


def _write_config(space_dir, cfg: HubConfig) -> None:
    from file_utils import atomic_write_text
    path = Path(space_dir) / CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(asdict(cfg), indent=2, sort_keys=True) + "\n")


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def enable(space_dir, *, hub_id: str, grantor: str, lease_ttl_s: int = DEFAULT_LEASE_TTL_S,
           cutover_hlc: str | None = None) -> HubConfig:
    """The owner opts a space in. `hub_id` is a stable name, not a hostname (E)."""
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
    cfg = HubConfig(hub_id=hub_id, grantor=grantor, lease_ttl_s=lease_ttl_s, cutover_hlc=cutover_hlc)
    _write_config(space_dir, cfg)
    return cfg


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
    cfg = replace(cfg, grantor=new_grantor, promoted_by=by, promoted_at=_iso_now(), promote_reason=reason)
    _write_config(space_dir, cfg)
    return cfg


def enable_pools(space_dir, on: bool = True) -> HubConfig:
    """The owner opts a hub space in to role pools (amendment F), or back out.

    Off by default. With pools off the hub keeps the assignee rule only, and
    records no budget. Commit the changed config.
    """
    cfg = load_config(space_dir)
    if cfg is None:
        raise HubRefused("hub_off", f"{space_dir} has no hub; pools need one (`enable` first)")
    cfg = replace(cfg, pools=bool(on))
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
    """Who may lease `item` when the space has not opted in to pools.

    Same rule as the dispatcher (ledger_claim.py, owner decision L4): only the
    exact writer the item is addressed to. An item addressed to nobody is not
    dispatched; an item carrying `role` waits for pools.
    """
    payload = getattr(item, "payload", None) or {}
    if payload.get("role"):
        return "pool items (role) are not enabled: roles, pools and budgets are amendment F"
    return _addressed(payload, actor)


def _addressed(payload: dict, actor: str) -> str | None:
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


# ── role pools (amendment F) ─────────────────────────────────────────────────

def role_template(role) -> str | None:
    """The role description an agent loads for `role`, as an installation-relative
    path, or None when there is none. `<role>.md` is the composed file; before
    it is built (DIP-0002 layers) the `<role>.base.md` layer is the one that exists.
    Looked up on every call, so a template added later is found."""
    if not isinstance(role, str) or not _ROLE.fullmatch(role):
        return None
    for name in (f"{role}.md", f"{role}.base.md"):
        if (ROLES_DIR / name).is_file():
            return f"{ROLES_REL}/{name}"
    return None


def registered_agent(actor: str) -> str | None:
    """None when `actor` writes for a principal of `kind: agent`; else why not."""
    from actor_identity import principal_of
    try:
        name, entry = principal_of(actor)
    except ValueError as exc:
        return f"cannot tell whether {actor!r} is an agent: {exc}"
    if not name:
        return f"{actor!r} belongs to no registered principal; only an agent picks up a pool task"
    if entry.get("kind") != "agent":
        return f"{actor!r} writes for {name!r}, which is not an agent principal"
    return None


def pool_rule(item, actor: str, state) -> str | None:
    """Eligibility in a space with pools on (owner decision 2026-10-18).

    An addressed task keeps the assignee rule, role or not: the main scenario.
    An unaddressed task with no role is still not dispatched. An unaddressed
    task with a role is a pool task: it needs a DONE_WHEN, and any registered
    agent principal may lease it (the request is already signed). The hub then
    checks the role description and the day's budget (`request_lease`), and
    one live lease per item is the hub's rule as for every item.
    """
    from .fold import done_when_of, pool_role
    payload = getattr(item, "payload", None) or {}
    if not pool_role(payload):
        return _addressed(payload, actor)
    if not done_when_of(payload):
        return "a pool task needs a DONE_WHEN before anyone may lease it"
    return registered_agent(actor)


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
        self.clock = clock or _wall_ms
        #: An explicit override, or None: the assignee rule, or `pool_rule`
        #: when the space has opted in to pools (decided per request).
        self.eligibility: Eligibility | None = eligibility
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

    def _state(self):
        from .fold import fold
        from .log import read_events
        events = read_events(self.space_dir)
        return events, fold(events)

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
            path, chain = _target(self.space_dir, log_name, event)
            existing = _parse_file(path)
            by_seq = {e.seq: e for e in existing}
            if type(event.seq) is int and event.seq in by_seq:
                if by_seq[event.seq].hash == event.hash:
                    return self._receipt("duplicate", cfg, chain, event)
                raise HubRefused("fork", f"{chain} seq {event.seq} is already a different event")
            expected = existing[-1].seq + 1 if existing else 0
            if event.seq != expected:
                raise HubRefused("gap", f"{chain} expects seq {expected}; replay from there", expected=expected)
            legacy = bool(cfg.cutover_hlc) and event.hlc < cfg.cutover_hlc
            if not event.sig and not legacy:
                raise HubRefused("unsigned", "the hub accepts only signed events (amendment D)")
            from .verify import GENESIS, check_events
            problems = check_events([(expected + 1, event)], registry_path=self.registry_path,
                                    strict=not legacy, log=chain,
                                    expected_prev=existing[-1].hash if existing else GENESIS,
                                    expected_seq=expected)
            problems = [p for p in problems if p.kind != "clock"]
            if problems:
                texts = [p.text for p in problems]
                code = ("bad_signature" if any("signature" in t for t in texts)
                        else "bad_hash" if any("hash mismatch" in t for t in texts) else "bad_chain")
                raise HubRefused(code, "; ".join(texts))
            not_counted = None
            if cfg.pools and event.type in ("item.complete", "item.verify"):
                # Accepted all the same: a signed fact refused here would strand
                # the writer's chain behind it. The fold does not count it, and
                # the receipt says so at once (HubWriter refuses before writing).
                from .fold import pool_refusal
                _, state = self._state()
                item = state.items.get((event.payload or {}).get("id") or "")
                not_counted = pool_refusal(item, event.type, event.actor) if item else None
            self._write_line(path, event)
            receipt = self._receipt("accepted", cfg, chain, event)
            if not_counted:
                receipt["not_counted"] = not_counted
            self._record_conflicts(event)
            return receipt

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
        if base_writer(event.actor) in {self.actor, cfg.grantor}:
            raise HubRefused("hub_log", "a hub's own log is written only by that hub")
        legacy = bool(cfg.cutover_hlc) and isinstance(event.hlc, str) and event.hlc < cfg.cutover_hlc
        if not legacy and event.hub != cfg.hub_id:
            raise HubRefused("wrong_hub", f"event belongs to hub {event.hub!r}, this is {cfg.hub_id!r}")

    def _write_line(self, path: Path, event: Event) -> None:
        from file_utils import fsync_directory
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+b") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                raw = f.read()
                if raw and not raw.endswith(b"\n"):
                    raise HubRefused("bad_chain", f"{path.name} ends in a torn line; not appending")
                f.write((to_line(event) + "\n").encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
                fsync_directory(path.parent)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

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
        recorded = {(e.payload.get("event") or {}).get("hash") for e in events
                    if e.type == "item.lease.conflict" and e.actor == cfg.grantor
                    and isinstance(e.payload.get("event"), dict)}
        by_hash = {e.hash: e for e in events}
        for h, why in sorted(item.lease_conflicts.items()):
            if h in recorded or h not in by_hash:
                continue
            bad = by_hash[h]
            self.log.append("item.lease.conflict", {
                "id": item_id, "reason": why,
                "event": {"log": getattr(bad, "log", None) or bad.actor, "seq": bad.seq, "hash": h,
                          "actor": bad.actor, "type": bad.type}})

    # ── leases ─────────────────────────────────────────────────────────────
    def _verify_request(self, req: dict, kind: str) -> str:
        from .keys import verify
        if not isinstance(req, dict) or req.get("kind") != kind:
            raise HubRefused("malformed", f"expected a signed {kind}")
        actor, sig, ts, nonce = req.get("actor"), req.get("sig"), req.get("ts_ms"), req.get("nonce")
        if not (isinstance(actor, str) and _NAME.fullmatch(actor) and isinstance(sig, str)
                and type(ts) is int and isinstance(nonce, str) and nonce):
            raise HubRefused("malformed", f"a {kind} carries actor, ts_ms, nonce and sig")
        if not verify(actor, _unsigned(req), sig, registry_path=self.registry_path):
            raise HubRefused("bad_signature", f"the {kind} is not signed by {actor!r}")
        now = self.clock()
        if abs(now - ts) > REQUEST_WINDOW_MS:
            raise HubRefused("stale_request", f"the {kind} is {abs(now - ts) // 1000}s from the hub's clock")
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
            actor = self._verify_request(req, "lease.request")
            if req.get("hub") != cfg.hub_id:
                raise HubRefused("wrong_hub", f"this is hub {cfg.hub_id!r}")
            item_id = req.get("item")
            _, state = self._state()
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
            if self.eligibility is not None:
                why = self.eligibility(item, actor, state)
            elif cfg.pools:
                why = pool_rule(item, actor, state)
            else:
                why = assignee_rule(item, actor, state)
            if why:
                raise HubRefused("not_eligible", why)
            role = (item.payload or {}).get("role")
            grant_role = None
            if cfg.pools and role:
                template = role_template(role)
                if template is None:
                    raise HubRefused("unknown_role", f"{role!r} has no role description in {ROLES_REL}/ "
                                     f"({role}.md); add one or change the task's role")
                grant_role = {"name": role, "template": template}
            from .fold import pool_role
            if cfg.pools and pool_role(item.payload):
                from .fold import pool_budget, pool_spent
                cap = pool_budget(state, cfg.hub_id, self.actor, str(role))
                spent = pool_spent(state, str(role), now)
                if cap is not None and spent >= cap:
                    raise HubRefused("budget_reached", f"the {role!r} pool has spent {spent} of its {cap} "
                                     "cents today; no new leases until tomorrow (UTC)",
                                     spent=spent, cap=cap)
            lease = {"id": item_id, "holder": actor, "granted_ms": now,
                     "expires_ms": now + cfg.lease_ttl_s * 1000,
                     "request": {"nonce": req["nonce"], "ts_ms": req["ts_ms"]}}
            if grant_role:
                lease["role"] = grant_role          # the description the holder loads
            event = self.log.append("item.lease", lease)
            return {"status": "granted", "lease": _event_dict(event), "log": self.log.log_name}

    def renew_lease(self, req: dict) -> dict:
        with self._mutex:
            cfg = self._writable(grant=True)
            actor = self._verify_request(req, "lease.renew")
            item_id = req.get("item")
            _, state = self._state()
            item = state.items.get(item_id) if isinstance(item_id, str) else None
            if item is None or item.lease_holder != actor or item.status != "claimed":
                raise HubRefused("not_holder", f"{actor} holds no lease on {item_id!r}")
            now = self.clock()
            event = self.log.append("item.lease.renew", {
                "id": item_id, "holder": actor, "renewed_ms": now, "expires_ms": now + cfg.lease_ttl_s * 1000,
                "lease": {k: (item.lease_ref or {}).get(k) for k in ("log", "seq", "hash")}})
            return {"status": "renewed", "lease": _event_dict(event), "log": self.log.log_name}

    # ── pool budget (amendment F) ───────────────────────────────────────────
    def _pool_writable(self) -> HubConfig:
        cfg = self._writable(grant=True)
        if not cfg.pools:
            raise HubRefused("pools_off", f"{self.space_dir} has not opted in to pools (`enable_pools`)")
        return cfg

    def set_budget(self, role: str, cents_per_day: int) -> dict:
        """The role pool's daily spending cap, in cents (0 closes the pool)."""
        with self._mutex:
            self._pool_writable()
            if role_template(role) is None:
                raise HubRefused("unknown_role", f"{role!r} has no role description in {ROLES_REL}/")
            if isinstance(cents_per_day, bool) or not isinstance(cents_per_day, int) or cents_per_day < 0:
                raise HubRefused("malformed", "cents_per_day is a non-negative integer")
            event = self.log.append("pool.budget", {"role": role, "space": self.space_dir.name,
                                                    "cents_per_day": cents_per_day})
            return {"status": "recorded", "event": _event_dict(event)}

    # ── the second confirmation's raw material ─────────────────────────────
    def seal(self) -> dict:
        """Seal the space as it stands (seal.py's shape). Publishing it is git's
        job (commit the events directory on the hub's schedule). The hub never
        checks its own seal."""
        from .seal import build_seal_payload
        with self._mutex:
            self._writable(grant=True)
            events, _ = self._state()
            try:
                payload = build_seal_payload(events)
            except ValueError as exc:
                raise HubRefused("unsealable", str(exc)) from None
            event = self.log.append("ledger.seal", payload)
            return {"status": "sealed", "seal": _event_dict(event), "log": self.log.log_name}

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
        return self.state_dir / "lease-receipts.jsonl"

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
        self._pool_precheck(type, payload)
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

    def _pool_precheck(self, type: str, payload: dict) -> None:
        """Refuse, before anything is written, a pool completion or check the
        fold would not count: the holder's own check, a completion without a
        DONE_WHEN. Judged on this writer's copy of the log plus its own lease
        receipts; what it cannot see the hub reports as `not_counted`."""
        if not self.cfg.pools or type not in ("item.complete", "item.verify"):
            return
        from .fold import fold, pool_refusal
        from .log import read_events
        iid = (payload or {}).get("id")
        item = fold(read_events(self.space_dir)).items.get(iid) if isinstance(iid, str) else None
        why = pool_refusal(item, type, self.actor) if item is not None else None
        if (not why and type == "item.verify" and (item is None or item.lease_holder is None)
                and any(r.get("item") == iid for r in self.receipts())):
            why = "own_check: the lease holder cannot check its own work; another actor must"
        if why:
            raise HubRefused(why.split(":", 1)[0], why)

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
        req = sign_request(self.actor, kind, {"hub": self.cfg.hub_id, "item": item_id},
                           keys_dir=self.keys_dir, clock=self.clock)
        try:
            answer = getattr(self.transport, method)(req)
        except HubUnavailable:
            self._mark_outage()
            raise
        if answer.get("status") in ("granted", "renewed"):
            self._accept_lease(answer["lease"], item_id)
        return answer

    def request_lease(self, item_id: str) -> dict:
        return self._ask("request_lease", "lease.request", item_id)

    def renew_lease(self, item_id: str) -> dict:
        return self._ask("renew_lease", "lease.renew", item_id)

    def _accept_lease(self, lease: dict, item_id: str) -> None:
        """First confirmation: check the hub's signed answer before relying on it."""
        from .keys import verify
        e = Event(**lease)
        if (e.hub != self.cfg.hub_id or (e.payload or {}).get("id") != item_id
                or (e.payload or {}).get("holder") != self.actor
                or not verify(e.actor, canonical_bytes(event_body(e)), e.sig, registry_path=self.registry_path)):
            raise HubRefused("bad_lease", "the hub's lease answer does not verify; not starting")
        with open(self._receipts_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"item": item_id, "log": e.actor, "event": lease}, sort_keys=True) + "\n")

    # ── second confirmation ────────────────────────────────────────────────
    def receipt_check(self, sealed_space, *, hub_actor: str | None = None) -> dict:
        """Are MY events and MY leases in the sealed log exactly as signed?

        `sealed_space`: a checkout of the space as published to git. Returns
        {ok, problems, pending}; ok is None when nothing of mine is sealed yet.
        Independent of the hub by construction: it compares the sealed copy with
        this writer's own log and its own record of the hub's answers.
        """
        from .keys import verify
        from .log import read_events
        from .seal import as_sequencer, latest_seal, verify_seal
        from .verify import check_events
        hub_actor = hub_actor or self.cfg.grantor
        events = read_events(Path(sealed_space))
        problems: list[str] = []
        pending: list[str] = []
        with as_sequencer(hub_actor):
            try:
                seal = latest_seal(events)
            except ValueError as exc:
                return {"ok": False, "problems": [f"unreadable seal: {exc}"], "pending": []}
            if seal is None:
                return {"ok": None, "problems": [], "pending": [f"no seal by {hub_actor} yet"]}
            ok, detail = verify_seal(events)
        if ok is False:
            problems.append(f"the seal does not verify: {detail}")
        sealed = {}
        for e in events:
            sealed.setdefault(getattr(e, "log", None) or e.actor, {})[e.seq] = e
        # The hub's seal itself must be the hub's, in an intact hub chain.
        hub_chain = sorted(sealed.get(hub_actor, {}).values(), key=lambda e: e.seq)
        for p in check_events(list(enumerate(hub_chain, 1)), registry_path=self.registry_path, strict=True):
            if p.kind != "clock":
                problems.append(f"hub log {hub_actor}: {p.text}")
        # My events, line for line, as I signed them.
        for chain, mine in self._local_chains():
            mark = seal.watermarks.get(chain, -1)
            copy = sealed.get(chain, {})
            for e in mine:
                if e.seq > mark:
                    pending.append(f"{chain} seq {e.seq} not sealed yet")
                    continue
                s = copy.get(e.seq)
                if s is None:
                    problems.append(f"{chain} seq {e.seq}: missing from the sealed log")
                elif to_line(s) != to_line(e):
                    problems.append(f"{chain} seq {e.seq}: differs from what I signed")
                elif not verify(self.actor, canonical_bytes(event_body(s)), s.sig, registry_path=self.registry_path):
                    problems.append(f"{chain} seq {e.seq}: my signature does not verify in the sealed log")
        # The leases the hub granted me, as it signed them at the first confirmation.
        for r in self.receipts():
            lease = Event(**r["event"])
            mark = seal.watermarks.get(r["log"], -1)
            if lease.seq > mark:
                pending.append(f"lease on {r['item']} (hub seq {lease.seq}) not sealed yet")
                continue
            s = sealed.get(r["log"], {}).get(lease.seq)
            if s is None or s.hash != lease.hash or to_line(s) != to_line(lease):
                problems.append(f"lease on {r['item']} (hub seq {lease.seq}) does not match the sealed log")
        return {"ok": not problems, "problems": problems, "pending": pending}


def strict_problems(events, cutover_hlc: str) -> list[str]:
    """Unsigned events after a hub space's cutover (amendment D: strict from there on)."""
    return [f"{getattr(e, 'log', None) or e.actor} seq {e.seq}: unsigned after cutover"
            for e in events if not e.sig and (not cutover_hlc or e.hlc >= cutover_hlc)]


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
    po = sub.add_parser("pools", help="opt a hub space in to (or out of) role pools (owner)")
    po.add_argument("space")
    po.add_argument("--off", action="store_true")
    sh = sub.add_parser("show", help="print a space's hub config")
    sh.add_argument("space")
    a = ap.parse_args(argv)
    if a.cmd == "enable":
        cfg = enable(a.space, hub_id=a.hub_id, grantor=a.grantor, lease_ttl_s=a.lease_ttl_s)
    elif a.cmd == "pools":
        cfg = enable_pools(a.space, on=not a.off)
    elif a.cmd == "promote":
        cfg = promote(a.space, a.grantor, by=a.by, reason=a.reason)
    else:
        cfg = load_config(a.space)
    print(json.dumps(asdict(cfg) if cfg else {"enabled": False}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
