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
  * AUTHORSHIP IS NOT AUTHORIZATION (finding 12): an append must also be an
    authorized transition under the existing policy (`ledger.hub_policy`:
    assignee, approver, co-sign, pool items only through a lease,
    arbitration). The hub refuses an unauthorized append (code
    `not_authorized`, an answer only), and the fold it reads its state from
    (`fold(..., policy=...)`) makes the same event a recorded no-op when it
    arrives by git, a restore or a rebuild.
  * A fresh request (lease, renewal, seal) is signed with the writer's CURRENT
    key, names this hub and space, is inside the time window and is answered
    once (finding 5, 6). Sealing over the network takes a signed request from
    a seal operator; the API token is transport only (finding 18).
  * A lease is granted only when no live lease exists, only by the ACTIVE hub
    (the grantor the space's config names), only to an eligible actor.
  * No automatic failover (H): a second hub instance refuses to grant (code
    `not_active_hub`) until the owner runs `promote`; a second process of the
    same hub on one host is locked out (`locked`). There is deliberately no
    code path that promotes by itself.
  * WHO GRANTS IS IN THE LEDGER (finding 4): `enable` and `promote` are
    owner-signed records (`hub.enable`, `hub.promote`) in the owner's log, and
    `authority()` derives the grantor epochs, fences and the cutover manifest
    from them; the config file is a cache. A promotion needs the old grantor
    fenced (its own `hub.fence`, or the owner's attestation that it stopped),
    names the predecessor's frontier and last seal, and makes the new grantor
    wait (`fencing_wait`) until every old-epoch lease expired plus the clock
    uncertainty. Old-grantor events after the frontier are not admitted
    (`fenced`), and new seals must extend the frontier.
  * RECOVERY BARRIER, not a fixed restart grace (finding 10). Request-level
    state -- nonces, the answers given, when each writer last reconciled --
    is persisted in the hub's state dir, so a retried request gets the same
    signed answer, after a restart too. A hub that lost it, or whose own log
    lost grants it gave (a restore from before them), refuses every grant
    (`recovering`) until writers present their stored grants (`reconcile`,
    restored byte for byte) and, for lost state, the owner clears it. An
    expired attempt is uncertain until its holder has reconciled after the
    expiry: the item is not granted to anyone else before that
    (`uncertain_attempt`), whether the hub restarted or not.
  * REPLAY AND REFUSED HISTORY (finding 11): appends are answered with a
    hub-signed acknowledgment of the log's frontier, and `status` carries a
    hub-signed frontier per log. A replaying writer checks its own event at
    the hub's frontier is the head the hub names before it skips anything,
    and stops on a different event (`diverged`). An event the hub refuses for
    what it is (hub-only or unknown type, not authorized) that is already in
    a writer's signed log is kept as a rejected proposal: stored so the chain
    stays continuous, recorded by a signed `ledger.reject`, no effect on
    state, cancellable only by `ledger.void`. Imports follow the event's own
    log (`chain`), not its type. Seals carry the authoritative state root
    (lease authority and policy), and receipts recompute it the same way.
  * ONE PROTOCOL PER SPACE (finding 17): a hub space declares its ledger
    protocol (config and the owner's `hub.enable`). Every reader
    (`read_events`) and writer (`EventLog`, `load_config`) of this code
    refuses a space whose protocol it does not support
    (`ProtocolUnsupported`), and `enable` refuses a space until every writer
    of it has declared support from its own host (`declare_ready`, signed
    and bound to its log's head; code `not_ready`).
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
from .events import OWNER_EVENT_TYPES
from .hub_admission import (GENESIS, HUB_ONLY_TYPES, PROPOSAL_CODES, PROTOCOL, Authority, Epoch, Refusal, admit, admit_chain,
                            check_event, rejected_proposals, signing_domain)
from .log import HubSigningRequired  # noqa: F401 -- re-exported: the hub's refusal to write unsigned

CONFIG = ".datacore/ledger-hub.json"
#: A signed request older or newer than this (hub clock) is refused.
REQUEST_WINDOW_MS = 5 * 60 * 1000
DEFAULT_LEASE_TTL_S = 900
#: Bounded wall-clock uncertainty between two grantors' clocks, added to the
#: last old-epoch lease expiry before a promoted grantor may grant (finding 4).
#: A deployment parameter the owner sets at `enable`; this default is not a
#: measured bound.
DEFAULT_CLOCK_UNCERTAINTY_MS = 120_000
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
    #: The grantor's promotion epoch (0 at `enable`, +1 per `promote`). Leases
    #: are ordered by (epoch, grantor-log seq) (audit finding 8). A CACHE of the
    #: owner's records (`owner_records`), which decide; never authority itself.
    epoch: int = 0
    #: The space's owner: signs `hub.enable` and `hub.promote` (finding 4).
    owner: str = ""
    #: The ledger protocol this space speaks (amendment I; finding 17). Every
    #: reader and writer must support it; see `declare_ready`.
    protocol: int = 2
    clock_uncertainty_ms: int = DEFAULT_CLOCK_UNCERTAINTY_MS


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
    if cfg.enabled:
        from .hub_admission import require_protocol
        require_protocol(cfg.protocol, str(space_dir))
    if not (_NAME.fullmatch(cfg.hub_id or "") and _NAME.fullmatch(cfg.grantor or "")):
        raise ValueError(f"{path}: hub_id and grantor must be lowercase names")
    if not _NAME.fullmatch(cfg.space_id or ""):
        raise ValueError(f"{path}: space_id must be a lowercase name (re-run `enable`)")
    if type(cfg.lease_ttl_s) is not int or cfg.lease_ttl_s <= 0:
        raise ValueError(f"{path}: lease_ttl_s must be a positive integer")
    if cfg.owner and not _NAME.fullmatch(cfg.owner):
        raise ValueError(f"{path}: owner must be a lowercase name")
    return cfg if cfg.enabled else None


def _write_config(space_dir, cfg: HubConfig) -> None:
    from file_utils import atomic_write_text
    path = Path(space_dir) / CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(asdict(cfg), indent=2, sort_keys=True) + "\n")


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def enable(space_dir, *, hub_id: str, grantor: str, owner: str, lease_ttl_s: int = DEFAULT_LEASE_TTL_S,
           cutover_hlc: str | None = None, space_id: str | None = None, keys_dir=None, registry_path=None,
           clock_uncertainty_ms: int = DEFAULT_CLOCK_UNCERTAINTY_MS) -> HubConfig:
    """The owner opts a space in. `hub_id` is a stable name, not a hostname (E).

    Also the owner's approval of the cutover: it records the cutover manifest
    (`cutover_logs`), each log's highest seq and head hash as they stand now.
    That history, and only that history, stays valid unsigned (audit finding
    1). Enabling again keeps the manifest and the space id unchanged.

    THE RECORD (audit finding 4, part-1 leftover): the manifest and the first
    grantor are also an owner-signed `hub.enable` event in the owner's own log.
    Admission takes them from that record; the config file is a cache, and a
    config edited to another grantor or manifest confers nothing.
    """
    if not _NAME.fullmatch(hub_id) or not _NAME.fullmatch(grantor) or not _NAME.fullmatch(owner or ""):
        raise ValueError("hub_id, grantor and owner must be lowercase names")
    if owner == grantor:
        raise ValueError("the owner approves the hub; it is not the grantor")
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
        # A MIXED-VERSION SPACE IS NOT SWITCHED ON (finding 17): every writer of
        # the space has declared, from its own host, that its code speaks this
        # protocol (`declare_ready`), bound to its log's current history.
        missing = _not_ready(space_dir, logs, registry_path)
        if missing:
            raise HubRefused("not_ready", "these writers have not declared support for ledger protocol "
                             f"{PROTOCOL}: {', '.join(missing)} (run `python3 -m ledger.hub ready` on each "
                             "writer's host)", writers=missing)
    cfg = HubConfig(hub_id=hub_id, grantor=grantor, lease_ttl_s=lease_ttl_s, cutover_hlc=cutover_hlc,
                    space_id=space_id, cutover_logs=logs, cutover_sha256=_manifest_sha(logs), owner=owner,
                    clock_uncertainty_ms=clock_uncertainty_ms, protocol=PROTOCOL)
    _write_config(space_dir, cfg)
    if not owner_records(space_dir, cfg, registry_path):
        from .log import EventLog
        EventLog(Path(space_dir), owner, keys_dir=keys_dir, registry_path=registry_path, sign=True).append(
            "hub.enable", {"space": space_id, "hub": hub_id, "epoch": 0, "grantor": grantor, "manifest": logs,
                           "manifest_sha256": cfg.cutover_sha256, "lease_ttl_s": lease_ttl_s,
                           "clock_uncertainty_ms": clock_uncertainty_ms, "protocol": PROTOCOL})
    return cfg


READY_DIR = ".datacore/hub-ready"


def declare_ready(space_dir, actor: str, *, keys_dir=None, registry_path=None) -> dict:
    """A writer's host states that its code speaks this protocol (finding 17).

    Run on each writer's own host before the owner enables the hub. The
    statement is signed with the writer's ledger key and bound to its log's
    current head, so it cannot be copied into another space or written on
    another writer's behalf."""
    from file_utils import atomic_write_text
    from .hub_admission import SUPPORTED_PROTOCOLS
    from .keys import ensure_keypair, sign
    from .log import read_events
    if not _NAME.fullmatch(actor):
        raise ValueError("actor must be a lowercase name")
    ensure_keypair(actor, keys_dir=keys_dir, registry_path=registry_path)
    mine = sorted((e for e in read_events(Path(space_dir)) if (getattr(e, "log", None) or e.actor) == actor
                   and type(e.seq) is int), key=lambda e: e.seq)
    st = {"kind": "protocol.ready", "actor": actor, "protocols": sorted(SUPPORTED_PROTOCOLS),
          "seq": mine[-1].seq if mine else -1, "head": mine[-1].hash if mine else GENESIS, "ts_ms": _wall_ms()}
    st["sig"] = sign(actor, canonical_bytes(st), keys_dir=keys_dir)
    path = Path(space_dir) / READY_DIR / f"{actor}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(st, indent=2, sort_keys=True) + "\n")
    return st


def _not_ready(space_dir, logs: dict, registry_path=None) -> list:
    """The writers of `logs` without a valid readiness statement for PROTOCOL."""
    from actor_identity import base_writer
    from .hub_admission import _writer_of
    from .keys import verify
    from .log import read_events
    by_chain: dict = {}
    for e in read_events(Path(space_dir)):
        by_chain.setdefault(getattr(e, "log", None) or e.actor, {})[e.seq] = e
    missing = []
    for writer in sorted({base_writer(_writer_of(c)) for c in logs}):
        try:
            st = json.loads((Path(space_dir) / READY_DIR / f"{writer}.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            missing.append(writer)
            continue
        body = canonical_bytes({k: v for k, v in st.items() if k != "sig"}) if isinstance(st, dict) else b""
        at = (by_chain.get(writer) or {}).get(st.get("seq")) if isinstance(st, dict) else None
        ok = (isinstance(st, dict) and st.get("kind") == "protocol.ready" and st.get("actor") == writer
              and PROTOCOL in (st.get("protocols") or [])
              and (st.get("seq") == -1 or (at is not None and at.hash == st.get("head")))
              and isinstance(st.get("sig"), str)
              and verify(writer, body, st["sig"], registry_path=registry_path))
        if not ok:
            missing.append(writer)
    return missing


def owner_records(space_dir, cfg: HubConfig, registry_path=None) -> list[Event]:
    """The owner's signed `hub.enable` / `hub.promote` records for this space and
    hub, in log order -- verified here: an intact chain, the owner's signature at
    the event's time, this space's domain. A forged or broken record raises."""
    from .keys import verify
    from .log import read_events
    if not cfg.owner:
        return []
    chain = sorted((e for e in read_events(Path(space_dir)) if (getattr(e, "log", None) or e.actor) == cfg.owner
                    and type(e.seq) is int), key=lambda e: e.seq)
    out, prev = [], GENESIS
    for i, e in enumerate(chain):
        if e.seq != i or e.prev != prev or compute_hash(event_body(e)) != e.hash:
            break                                       # nothing after a break is the owner's
        prev = e.hash
        if e.type not in OWNER_EVENT_TYPES:
            continue
        payload = e.payload if isinstance(e.payload, dict) else {}
        if payload.get("space") != cfg.space_id or payload.get("hub") != cfg.hub_id:
            continue
        if (e.actor != cfg.owner or e.hub != cfg.hub_id
                or e.domain != signing_domain(cfg.space_id, cfg.hub_id, cfg.owner)
                or not e.sig or not verify(e.actor, canonical_bytes(event_body(e)), e.sig,
                                           registry_path=registry_path, at_ms=_physical_ms(e.hlc))):
            raise HubRefused("bad_record", f"{cfg.owner} seq {e.seq}: a {e.type} that is not the owner's signed record")
        out.append(e)
    return out


def epochs_of(records: list[Event]) -> tuple:
    """The grantor epochs the owner's records define, oldest first."""
    if not records or records[0].type != "hub.enable":
        raise HubRefused("no_owner_record", "this space has no owner-signed hub.enable record; "
                         "re-run `enable --owner` (the cutover is the owner's act, recorded in the ledger)")
    eps = [Epoch(0, records[0].payload.get("grantor", ""))]
    for r in records[1:]:
        if r.type != "hub.promote":
            continue
        p = r.payload
        if p.get("epoch") != len(eps) or p.get("previous") != eps[-1].grantor or not isinstance(p.get("grantor"), str):
            raise HubRefused("bad_record", f"promotion record seq {r.seq} does not follow epoch {len(eps) - 1}")
        frontier = ((p.get("predecessor") or {}).get("frontier") or {}).get(eps[-1].grantor) or {}
        eps[-1] = replace(eps[-1], fence_seq=int(frontier.get("seq", -1)), fence_head=frontier.get("head"))
        nb = p.get("not_before_ms")
        eps.append(Epoch(p["epoch"], p["grantor"], not_before_ms=nb if type(nb) is int else None))
    return tuple(eps)


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
    """The explicit input admission and the fold take, read once before replay.

    Who grants, in which epoch, up to which fence, and the cutover manifest
    all come from the owner's signed records (audit finding 4), never from the
    config file: a config that disagrees with them is refused (`bad_manifest`)
    or ignored (its `grantor`)."""
    legacy = load_manifest(space_dir, cfg)
    records = owner_records(space_dir, cfg, registry_path)
    eps = epochs_of(records)
    enabled = records[0].payload
    if enabled.get("manifest_sha256") != cfg.cutover_sha256 or enabled.get("manifest") != cfg.cutover_logs:
        raise HubRefused("bad_manifest", f"{Path(space_dir) / CONFIG}: the cutover manifest is not the one "
                         "the owner signed (hub.enable)")
    return Authority(space_id=cfg.space_id, hub_id=cfg.hub_id, grantors=frozenset(ep.grantor for ep in eps),
                     legacy=legacy, registry_path=registry_path, owner=cfg.owner, epochs=eps)


def promote(space_dir, new_grantor: str, *, by: str, reason: str, keys_dir=None, registry_path=None,
            attest_stopped: bool = False, clock: Callable[[], int] | None = None) -> HubConfig:
    """The owner's one command that moves lease granting to another hub (H).

    Explicit by design: nothing in this module calls it. It is recorded in the
    ledger as an owner-signed `hub.promote` naming the new grantor, the new
    epoch, and the predecessor's fence, frontier and last seal (audit finding
    4). Preconditions:

      * the old grantor is FENCED: its own signed `hub.fence` (it stopped), or
        -- when it cannot be reached -- `attest_stopped=True`, the owner's
        statement that it is stopped, with the frontier the owner's copy
        holds. Neither: refused (`unfenced`); leases stay paused, on purpose;
      * the new grantor grants nothing before `not_before_ms`: the last expiry
        of any old-epoch lease within the frontier (and, when only attested,
        now plus one lease period) plus the clock uncertainty. A higher epoch
        cannot stop an old holder already working; the wait can;
      * events of the old grantor after the frontier are not admitted anywhere
        the record is known, and every new seal extends the frontier.

    Commit the owner's log and the config so every copy learns of it.
    """
    from .log import EventLog, read_events
    cfg = load_config(space_dir)
    if cfg is None:
        raise HubRefused("hub_off", f"{space_dir} has no hub to promote")
    if not _NAME.fullmatch(new_grantor):
        raise ValueError("grantor must be a lowercase name")
    if not by or not reason:
        raise ValueError("a promotion names who ordered it and why")
    if by != cfg.owner:
        raise HubRefused("not_owner", f"only the space's owner ({cfg.owner or 'none recorded'}) promotes")
    auth = authority(space_dir, cfg, registry_path)
    old = auth.active
    if new_grantor in auth.grantors or new_grantor == cfg.owner:
        raise ValueError("promote a new grantor name; a retired grantor or the owner is never promoted")
    now = (clock or _wall_ms)()
    chain = sorted((e for e in read_events(Path(space_dir)) if (getattr(e, "log", None) or e.actor) == old.grantor
                    and type(e.seq) is int), key=lambda e: e.seq)
    n, _ = admit_chain(old.grantor, chain, auth, now_ms=now)
    admitted = chain[:n]
    fence = next((e for e in admitted if e.type == "hub.fence" and (e.payload or {}).get("epoch") == old.epoch), None)
    if fence is not None:
        mode, last = "grantor", fence
    elif attest_stopped:
        mode, last = "owner-attested", (admitted[-1] if admitted else None)
    else:
        raise HubRefused("unfenced", f"{old.grantor} has not fenced itself (`hub fence`), and the owner has "
                         "not attested that it is stopped: no promotion, leases stay paused")
    upto = admitted[: admitted.index(last) + 1] if last is not None else []
    expiries = [e.payload["expires_ms"] for e in upto if e.type in ("item.lease", "item.lease.renew")
                and type((e.payload or {}).get("expires_ms")) is int]
    if mode == "owner-attested":
        expiries.append(now + cfg.lease_ttl_s * 1000)
    seal = next((e for e in reversed(upto) if e.type == "ledger.seal"), None)
    payload = {
        "space": cfg.space_id, "hub": cfg.hub_id, "epoch": old.epoch + 1, "grantor": new_grantor,
        "previous": old.grantor, "reason": reason,
        "fence": {"mode": mode, "event": {"seq": fence.seq, "hash": fence.hash} if fence else None},
        "predecessor": {
            "frontier": {old.grantor: {"seq": last.seq if last else -1, "head": last.hash if last else GENESIS}},
            "last_seal": {"seq": seal.seq, "hash": seal.hash} if seal else None},
        "not_before_ms": (max(expiries) + cfg.clock_uncertainty_ms) if expiries else None,
    }
    EventLog(Path(space_dir), by, keys_dir=keys_dir, registry_path=registry_path, sign=True).append(
        "hub.promote", payload)
    previous = tuple(g for g in dict.fromkeys((*cfg.previous_grantors, cfg.grantor)) if g != new_grantor)
    cfg = replace(cfg, grantor=new_grantor, promoted_by=by, promoted_at=_iso_now(), promote_reason=reason,
                  previous_grantors=previous, epoch=old.epoch + 1)
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


def _target(space: Path, log_name: str, event: Event, chain: str | None = None) -> tuple[Path, str]:
    """The file a writer's event lives in, and the chain key readers use for it.

    `chain` (the writer names the exact log the event lies in) decides; the
    event's domain binds it, so a wrong one is refused at admission. Without
    it, the event's type decides, as before -- which misplaces a telemetry-type
    event that lies in a main log from before the split (finding 11)."""
    from .log import TELEMETRY_SUFFIX, is_telemetry, log_paths
    main, telemetry = log_paths(space, log_name)
    if chain is not None:
        if chain == log_name:
            return main, log_name
        if chain == log_name + TELEMETRY_SUFFIX:
            return telemetry, chain
        raise HubRefused("malformed", f"chain {chain!r} is not a log of {log_name!r}")
    if is_telemetry(event.type, event.payload):
        return telemetry, log_name + TELEMETRY_SUFFIX
    return main, log_name


class Hub:
    """One hub instance serving one space. Thread-safe within a process."""

    def __init__(self, space_dir, actor: str, *, keys_dir=None, registry_path=None, state_dir=None,
                 clock: Callable[[], int] | None = None, eligibility: Eligibility | None = None,
                 policy=None):
        self.space_dir = Path(space_dir)
        cfg = load_config(self.space_dir)
        if cfg is None:
            raise HubRefused("hub_off", f"{self.space_dir} has not opted in to a hub ({CONFIG})")
        if not _NAME.fullmatch(actor):
            raise ValueError(f"invalid hub actor {actor!r}")
        self.actor = actor
        self.keys_dir, self.registry_path = keys_dir, registry_path
        # A hub over an edited manifest, or without the owner's signed records, does not start.
        authority(self.space_dir, cfg, registry_path)
        self.clock = clock or _wall_ms
        self.eligibility: Eligibility = eligibility or assignee_rule
        # Authorization's explicit input (audit finding 12), read ONCE here --
        # never by the fold halfway through a replay. A `ledger.policy.Policy`
        # is converted; None reads the installation's policy and registry.
        from .hub_policy import PolicyInputs
        self.policy = (policy if isinstance(policy, PolicyInputs)
                       else PolicyInputs.load(policy))
        self.started_ms = self.clock()
        self.closed = False
        self._mutex = threading.RLock()
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
        # REQUEST-LEVEL STATE IS NOT A PROJECTION (amendment A, H; finding 10):
        # nonces, the answers given and when each writer last reconciled are
        # persisted here, so a retry gets the same signed answer after a
        # restart. A hub that has lost them runs the recovery barrier.
        self._requests_path = state / f"{key}.requests.jsonl"
        self._reconciled_path = state / f"{key}.reconciled.json"
        self._answers: dict[str, dict] = {}
        self._reconciled: dict[str, int] = {}
        self.barrier: dict = {}
        if self._lock_fh is not None:
            self._load_request_state()

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
        if grant:
            # WHO GRANTS is the owner's records, not the config (finding 4).
            active = self._authority(cfg).active
            if active.grantor != self.actor:
                raise HubRefused("not_active_hub", f"this hub ({self.actor}) does not grant for this space; "
                                 f"{active.grantor} does (epoch {active.epoch}). Moving that is the owner's "
                                 "`promote` command, never automatic", active=active.grantor)
            if self._fenced(active.epoch):
                raise HubRefused("fenced", f"this hub ({self.actor}) fenced itself for epoch {active.epoch}; "
                                 "it grants, renews and seals nothing more")
        return cfg

    def _fenced(self, epoch: int) -> bool:
        return any(e.type == "hub.fence" and (e.payload or {}).get("epoch") == epoch
                   for e in _parse_file(self.log.path))

    def fence(self, reason: str) -> dict:
        """This grantor stops (finding 4): a signed `hub.fence`, the last event of
        its epoch that counts. The owner's `promote` names it as the frontier."""
        if not reason:
            raise ValueError("a fence says why")
        with self._mutex:
            cfg = self._writable(grant=True)
            epoch = self._authority(cfg).active.epoch
            event = self.log.append("hub.fence", {"space": cfg.space_id, "hub": cfg.hub_id, "epoch": epoch,
                                                  "reason": reason})
            return _event_dict(event)

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
        return events, fold(events, lease_grantors=auth.lease_grantors(), policy=self.policy)

    def _fold_kwargs(self, cfg: HubConfig) -> dict:
        return {"lease_grantors": self._authority(cfg).lease_grantors(), "policy": self.policy}

    def refusals(self) -> dict:
        """{log: where and why it stops being admitted} -- for status and verify."""
        from .log import read_events
        return admit(read_events(self.space_dir), self._authority(self.config()), now_ms=self.clock()).refused

    # ── appends (first confirmation) ───────────────────────────────────────
    def append(self, submission: dict) -> dict:
        """Accept one signed writer event: verify, chain, store, answer.

        `submission`: {event, log, chain?, rejected_proposal?}. `chain` is the
        exact log the event lies in (its domain names it), so an import follows
        the event's own log rather than its type (audit finding 11).
        `rejected_proposal: true` asks the hub to keep an event it would refuse
        for what it IS (a hub-only or unknown type, or an unauthorized
        transition) because it is already in the writer's signed log: it is
        stored so the chain stays continuous, recorded by a signed
        `ledger.reject`, and has no effect on state (status `rejected`)."""
        raw = submission.get("event", submission) if isinstance(submission, dict) else None
        if not isinstance(raw, dict):
            raise HubRefused("malformed", "expected {event: {...}, log?: name}")
        try:
            event = Event(**raw)
        except TypeError as exc:
            raise HubRefused("malformed", f"not an event: {exc}") from None
        sub = submission if isinstance(submission, dict) else {}
        log_name = sub.get("log") or event.actor
        proposal = sub.get("rejected_proposal") is True
        with self._mutex:
            cfg = self._writable(grant=proposal)          # only the grantor records a rejection
            self._check_submission(event, log_name, cfg, proposal=proposal)
            if not event.sig:
                # Network admission never admits legacy: unsigned history comes by
                # git and counts only as the cutover manifest names it (finding 1).
                raise HubRefused("unsigned", "the hub accepts only signed events (amendment D)")
            auth = self._authority(cfg)
            path, chain = _target(self.space_dir, log_name, event, sub.get("chain"))
            from file_utils import fsync_directory
            from .log import CorruptLogError, _parse_log_bytes
            if not path.exists() and self._onto(event, chain, [], cfg, auth, proposal) == "duplicate":
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
                    verdict = self._onto(event, chain, existing, cfg, auth, proposal)
                    if verdict == "duplicate":
                        return self._receipt("duplicate", cfg, chain, event)
                    f.write((to_line(event) + "\n").encode("utf-8"))
                    f.flush()
                    os.fsync(f.fileno())
                    fsync_directory(path.parent)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            if verdict is not None:
                code, why = verdict
                self.log.append("ledger.reject", {
                    "event": {"log": chain, "seq": event.seq, "hash": event.hash, "actor": event.actor,
                              "type": event.type},
                    "code": code, "reason": why})
                return self._receipt("rejected", cfg, chain, event, reason=f"{code}: {why}")
            receipt = self._receipt("accepted", cfg, chain, event)
            self._record_conflicts(event)
            return receipt

    def _onto(self, event: Event, chain: str, existing: list, cfg: HubConfig, auth: Authority,
              proposal: bool = False):
        """May `event` be appended onto `existing` (the chain as the file holds it)?

        "duplicate" when it is already there; None when it is an admissible,
        authorized append; (code, why) when `proposal` and it is a signed,
        chained event the hub keeps only as a rejected proposal. Raises
        HubRefused otherwise."""
        by_seq = {e.seq: e for e in existing}
        if type(event.seq) is int and event.seq in by_seq:
            if by_seq[event.seq].hash == event.hash:
                return "duplicate"
            raise HubRefused("fork", f"{chain} seq {event.seq} is already a different event")
        expected = existing[-1].seq + 1 if existing else 0
        if event.seq != expected:
            raise HubRefused("gap", f"{chain} expects seq {expected}; replay from there", expected=expected)
        now = self.clock()
        _, why = admit_chain(chain, existing, auth, now_ms=now, rejected=frozenset(self._rejected()))
        if why:
            raise HubRefused("chain_refused", f"{chain} is not admissible from {why}; "
                             "nothing more is accepted onto it")
        prior = existing[-1] if existing else None
        try:
            check_event(event, chain, prior, auth, now_ms=now, current_key=True)
        except Refusal as exc:
            if not (proposal and exc.code in PROPOSAL_CODES):
                raise HubRefused(exc.code, exc.message) from None
            try:   # everything but what it IS must still hold: signed, chained, this domain
                check_event(event, chain, prior, auth, now_ms=now, as_proposal=True)
            except Refusal as inner:
                raise HubRefused(inner.code, inner.message) from None
            return (exc.code, exc.message)
        try:
            self._authorize(event)
        except HubRefused as exc:
            if proposal and exc.code == "not_authorized":
                return (exc.code, exc.message)
            raise
        return None

    def _rejected(self) -> set:
        """(chain, seq, hash) this space's grantors recorded as rejected proposals."""
        from .log import read_events
        auth = self._authority(self.config())
        return rejected_proposals([e for e in read_events(self.space_dir)
                                   if e.actor in auth.grantors and (getattr(e, "log", None) or e.actor) == e.actor])

    def _authorize(self, event: Event) -> None:
        """Authorship is not authorization (audit finding 12): the same rule the
        fold applies on replay (`hub_policy.authorize`), asked before anything
        is stored. A refusal is an answer only."""
        from .hub_policy import authorize
        events, state = self._state()
        why = authorize(event, state, {e.hash: e for e in events}, self.policy)
        if why:
            raise HubRefused("not_authorized", why)

    def _check_submission(self, event: Event, log_name: str, cfg: HubConfig, *, proposal: bool = False) -> None:
        if not isinstance(log_name, str) or not _NAME.fullmatch(log_name):
            raise HubRefused("malformed", f"invalid log name {log_name!r}")
        if event.type not in EVENT_TYPES and not proposal:
            raise HubRefused("unknown_type", f"unknown event type {event.type!r}")
        if event.type in HUB_ONLY_TYPES and not proposal:
            raise HubRefused("hub_only_type", f"{event.type} is written only by the hub")
        from actor_identity import base_writer
        if not isinstance(event.actor, str) or base_writer(log_name) != base_writer(event.actor):
            raise HubRefused("foreign_log", f"{event.actor!r} may append only to its own log, not {log_name!r}")
        if base_writer(event.actor) in {self.actor, cfg.grantor, *cfg.previous_grantors}:
            raise HubRefused("hub_log", "a hub's own log is written only by that hub")
        # Everything else -- signature, hub, domain, clock, chain -- is the one
        # admission rule (ledger.hub_admission.check_event), applied in `append`.

    def _receipt(self, status: str, cfg: HubConfig, chain: str, event: Event, **extra) -> dict:
        """The append answer: a hub-signed statement of the writer log's frontier
        after the append -- log, seq and head hash (amendment A; finding 11)."""
        out = {"status": status, "hub": cfg.hub_id, "log": chain, "seq": event.seq, "hash": event.hash, **extra}
        out["ack"] = self._signed({"kind": "append.ack", "space": cfg.space_id, "hub": cfg.hub_id, "log": chain,
                                   "seq": event.seq, "head": event.hash, "status": status})
        return out

    def _signed(self, statement: dict) -> dict:
        from .keys import sign
        statement = {**statement, "signer": self.actor, "ts_ms": self.clock()}
        return {**statement, "sig": sign(self.actor, canonical_bytes(statement), keys_dir=self.keys_dir)}

    def frontiers(self) -> dict:
        """A hub-signed statement of every log's frontier this hub holds
        ({log: {seq, head}}): what a replaying writer compares its own log
        with before it skips anything (audit finding 11)."""
        cfg = self.config()
        events, _ = self._state()
        logs: dict = {}
        for e in events:
            chain = getattr(e, "log", None) or e.actor
            if type(e.seq) is int and (chain not in logs or e.seq > logs[chain]["seq"]):
                logs[chain] = {"seq": e.seq, "head": e.hash}
        return self._signed({"kind": "frontier", "space": cfg.space_id, "hub": cfg.hub_id,
                             "logs": dict(sorted(logs.items()))})

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
        return actor

    # ── request-level state: idempotent answers, reconciliation, barrier ───
    def _load_request_state(self) -> None:
        own = {e.seq: e for e in _parse_file(self.log.path)}
        try:
            lines = self._requests_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = None
        for ln in lines or []:
            try:
                rec = json.loads(ln)
                self._answers[rec["nonce"]] = rec
            except (ValueError, KeyError, TypeError):
                continue                          # a torn last line: the answer was never given
        try:
            self._reconciled = {str(k): int(v) for k, v in
                                json.loads(self._reconciled_path.read_text(encoding="utf-8")).items()}
        except (FileNotFoundError, ValueError, TypeError, AttributeError):
            self._reconciled = {}
        if lines is None and own:
            self.barrier["state_lost"] = ("this hub's request state is missing while its log holds answers; "
                                          "the owner clears recovery once every writer has reconciled")
        else:
            self._requests_path.touch()
        missing = self._missing_own()
        if missing:
            self.barrier["own_log_behind"] = missing

    def _missing_own(self) -> list:
        """Grants this hub answered that its own log no longer holds (a restore
        from a copy taken before them): [(seq, hash)]."""
        own = {e.seq: e.hash for e in _parse_file(self.log.path)}
        out = []
        for rec in self._answers.values():
            ev = (rec.get("answer") or {}).get("lease")
            if isinstance(ev, dict) and ev.get("actor") == self.actor and own.get(ev.get("seq")) != ev.get("hash"):
                out.append((ev.get("seq"), ev.get("hash")))
        return sorted(set(out))

    def _answered(self, req: dict):
        """The stored answer to this exact request, or None when it is new.
        The same nonce with another body is a replay, refused."""
        rec = self._answers.get(req.get("nonce"))
        if rec is None:
            return None
        if rec.get("digest") != request_digest(req):
            raise HubRefused("replayed_request", "this request was already answered")
        if "refusal" in rec:
            r = rec["refusal"]
            raise HubRefused(r["code"], r.get("message", ""), **{k: v for k, v in r.items()
                                                                 if k not in ("code", "message")})
        return rec["answer"]

    def _remember(self, req: dict, *, answer: dict | None = None, refusal: HubError | None = None) -> None:
        rec = {"nonce": req["nonce"], "digest": request_digest(req), "kind": req.get("kind"),
               "ts_ms": self.clock()}
        if refusal is not None:
            rec["refusal"] = refusal.as_dict()
        else:
            rec["answer"] = answer
        with open(self._requests_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._answers[rec["nonce"]] = rec

    def _once(self, req: dict, kind: str, cfg: HubConfig, body: Callable[[str], dict]) -> dict:
        """Verify, then answer `req` exactly once: a retry gets the stored answer."""
        actor = self._verify_request(req, kind, cfg)
        cached = self._answered(req)
        if cached is not None:
            return cached
        try:
            answer = body(actor)
        except HubRefused as exc:
            if exc.code not in ("locked", "not_active_hub"):
                self._remember(req, refusal=exc)
            raise
        self._remember(req, answer=answer)
        return answer

    def request_lease(self, req: dict) -> dict:
        """Grant `req["item"]` to the signing actor if no live lease exists.
        Answered once: a retried request gets the same signed answer (finding 10)."""
        with self._mutex:
            cfg = self._writable(grant=True)
            return self._once(req, "lease.request", cfg, lambda actor: self._grant(req, actor, cfg))

    def _grant(self, req: dict, actor: str, cfg: HubConfig) -> dict:
        from actor_identity import base_writer
        from .fold import lease_live, task_content_hash
        active = self._authority(cfg).active
        item_id = req.get("item")
        events, state = self._state()
        item = state.items.get(item_id) if isinstance(item_id, str) else None
        if item is None:
            raise HubRefused("unknown_item", f"no item {item_id!r} in this space")
        now = self.clock()
        if item.status in ("completed", "verified", "dismissed", "archived"):
            raise HubRefused("closed", f"{item_id} is {item.status}")
        if self.barrier:
            # THE RECOVERY BARRIER (finding 10): after a restart that lost
            # request state, or a restore that lost grants this hub gave,
            # nothing is granted until the sources are reconciled.
            raise HubRefused("recovering", "the hub is reconciling its log with the writers' logs and receipts "
                             "before it grants again", barrier=sorted(self.barrier))
        if active.not_before_ms is not None and now < active.not_before_ms:
            raise HubRefused("fencing_wait", f"epoch {active.epoch} grants from {active.not_before_ms}: "
                             "every lease of the previous epoch must have expired, plus the clock uncertainty",
                             retry_after_ms=active.not_before_ms)
        if item.lease_unresolved:
            raise HubRefused("owner_resolution", f"{item_id} has a result that arrived after its lease "
                             "attempt ended; the owner decides before it is granted again",
                             events=sorted(item.lease_unresolved))
        if lease_live(item, now):
            if item.lease_holder == actor:
                return {"status": "held", "lease": dict(item.lease_ref or {}),
                        "expires_ms": item.lease_expires_ms}
            raise HubRefused("taken", f"{item_id} is leased to {item.lease_holder}",
                             holder=item.lease_holder, expires_ms=item.lease_expires_ms)
        ref = item.lease_ref or {}
        previous = ref.get("holder") or item.lease_holder
        if ref and previous and base_writer(previous) != base_writer(actor):
            # AN EXPIRED ATTEMPT IS UNCERTAIN until its holder has reconciled
            # after the expiry (its results are in) or the owner resolved it.
            # This replaces the fixed restart grace, which missed a holder away
            # longer than one lease and a partition with no restart (finding 10).
            if item.lease_holder is not None:
                since = item.lease_expires_ms
            elif ref.get("ended") and ref.get("end_reason") == "expired":
                since = ref.get("ended_ms")
            else:
                since = None                          # released, or resolved by the owner
            if since is not None and self._reconciled.get(base_writer(previous), -1) < since:
                raise HubRefused("uncertain_attempt", f"{item_id}'s lease to {previous} expired while it may "
                                 "still be working; it reconciles (replays) first, or the owner resolves it",
                                 holder=previous)
        if item.status != "created" and item.lease_holder is None:
            raise HubRefused("claimed", f"{item_id} is {item.status} outside a lease (owner {item.owner})")
        if item.edit_conflicts:
            raise HubRefused("conflicted", f"{item_id} has unresolved edit conflicts")
        why = self.eligibility(item, actor, state)
        if why:
            raise HubRefused("not_eligible", why)
        # The answer sorts after every event of its item (audit finding 7),
        # whatever the sibling logs' tails say.
        floor = _item_floor(events, item_id)
        # EXPIRY IS EXPLICIT (audit finding 9): the previous attempt ends by
        # a signed event before anything is granted again.
        if _open_attempt(item):
            floor = self._expire(item, events, now, floor,
                                 "released" if item.lease_holder is None else "expired").hlc
        event = self.log.append("item.lease", {
            "id": item_id, "holder": actor, "attempt": attempt_id(req),
            "task_hash": task_content_hash(item.payload), "epoch": active.epoch,
            "granted_ms": now, "expires_ms": now + cfg.lease_ttl_s * 1000,
            "request": _request_ref(req)}, after=floor)
        return {"status": "granted", "lease": _event_dict(event), "log": self.log.log_name}

    def reconcile(self, req: dict) -> dict:
        """A writer's side of the recovery barrier (finding 10): a signed request
        carrying its per-log frontier and the hub-signed grants it stored.

        * Each stored grant of THIS hub that its own log no longer holds (a
          restore from before it) is restored, byte for byte, when it chains
          onto the log's head; a different event at that seq is `diverged`.
        * The writer's frontier must equal the hub's copy of its logs (it
          replays first); otherwise `behind`.
        * The writer is then reconciled as of now: its expired attempts are
          no longer uncertain."""
        from actor_identity import base_writer
        from .keys import verify
        with self._mutex:
            cfg = self._writable(grant=False)
            actor = self._verify_request(req, "reconcile", cfg)
            cached = self._answered(req)
            if cached is not None:
                return cached
            restored = []
            for raw in sorted((r for r in req.get("leases") or [] if isinstance(r, dict)),
                              key=lambda r: r.get("seq") if type(r.get("seq")) is int else -1):
                try:
                    ev = Event(**raw)
                except TypeError:
                    continue
                if ev.actor != self.actor:
                    continue
                own = _parse_file(self.log.path)
                held = {e.seq: e for e in own}
                if ev.seq in held:
                    if held[ev.seq].hash != ev.hash:
                        raise HubRefused("diverged", f"{self.actor} seq {ev.seq} is a different event than the "
                                         "grant the writer stored; this goes to the owner", seq=ev.seq)
                    continue
                head = own[-1] if own else None
                if ev.seq != (head.seq + 1 if head else 0) or ev.prev != (head.hash if head else GENESIS):
                    continue
                if compute_hash(event_body(ev)) != ev.hash or not verify(
                        self.actor, canonical_bytes(event_body(ev)), ev.sig, registry_path=self.registry_path,
                        at_ms=_physical_ms(ev.hlc)):
                    raise HubRefused("bad_lease", f"the stored grant at seq {ev.seq} is not this hub's signature")
                self._restore_own(ev)
                restored.append(ev.seq)
            frontier = self.frontiers()["logs"]
            for chain, mine in (req.get("frontier") or {}).items():
                held = frontier.get(chain) or {"seq": -1, "head": GENESIS}
                if not isinstance(mine, dict) or (mine.get("seq"), mine.get("head")) != (held["seq"], held["head"]):
                    raise HubRefused("behind", f"the hub holds {chain} up to seq {held['seq']}; replay first",
                                     log=chain, seq=held["seq"])
            self._reconciled[base_writer(actor)] = self.clock()
            from file_utils import atomic_write_text
            atomic_write_text(self._reconciled_path, json.dumps(self._reconciled, sort_keys=True))
            missing = self._missing_own()
            if missing:
                self.barrier["own_log_behind"] = missing
            else:
                self.barrier.pop("own_log_behind", None)
            answer = {"status": "reconciled", "restored": restored, "barrier": sorted(self.barrier)}
            self._remember(req, answer=answer)
            return answer

    def _restore_own(self, ev: Event) -> None:
        """Put back a grant this hub signed and its log lost, byte for byte, under
        the file lock -- never re-signed, never a new seq."""
        from file_utils import fsync_directory
        path = self.log.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+b") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                raw = f.read()
                own = [from_line(ln) for ln in raw.decode("utf-8").splitlines() if ln.strip()]
                head = own[-1] if own else None
                if ev.seq != (head.seq + 1 if head else 0) or ev.prev != (head.hash if head else GENESIS):
                    return
                f.write((to_line(ev) + "\n").encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
                fsync_directory(path.parent)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def clear_recovery(self, req: dict) -> dict:
        """The owner ends a `state_lost` barrier (signed `recovery.clear`), after
        the writers have reconciled: the hub cannot know whom a lost answer went to."""
        with self._mutex:
            cfg = self._writable(grant=False)
            actor = self._verify_request(req, "recovery.clear", cfg)
            if actor != cfg.owner:
                raise HubRefused("not_owner", f"only the space's owner ({cfg.owner}) clears recovery")
            self.barrier.pop("state_lost", None)
            self._requests_path.touch()
            return {"status": "cleared", "barrier": sorted(self.barrier)}

    def _expire(self, item, events, now: int, floor, reason: str) -> Event:
        """Write `item.lease.expire` for `item`'s open attempt, with the holder-log
        frontier this hub holds: a result beyond it was admitted after the expiry."""
        from actor_identity import base_writer
        from .hub_admission import _writer_of
        ref = item.lease_ref or {}
        holder = ref.get("holder") or item.lease_holder or ""
        frontier: dict = {}
        for e in events:
            chain = getattr(e, "log", None) or e.actor
            if holder and _writer_of(chain) == base_writer(holder) and type(e.seq) is int:
                frontier[chain] = max(frontier.get(chain, -1), e.seq)
        return self.log.append("item.lease.expire", {
            "id": item.id, "holder": holder, "attempt": ref.get("attempt"), "expired_ms": now,
            "reason": reason, "frontier": dict(sorted(frontier.items())),
            "lease": {k: ref.get(k) for k in ("log", "seq", "hash")}}, after=floor)

    def expire_due(self) -> list[dict]:
        """End every lease attempt whose expiry has passed (the hub's scheduled sweep).
        Only expiry: granting again is a separate request."""
        from .fold import lease_live
        with self._mutex:
            self._writable(grant=True)
            events, state = self._state()
            now = self.clock()
            out = []
            for item in sorted(state.items.values(), key=lambda i: i.id):
                if item.lease_holder is not None and _open_attempt(item) and not lease_live(item, now):
                    out.append(_event_dict(self._expire(item, events, now, _item_floor(events, item.id), "expired")))
            return out

    def renew_lease(self, req: dict) -> dict:
        """Extend the SAME live attempt; never resurrect an expired one (audit finding 9)."""
        with self._mutex:
            cfg = self._writable(grant=True)
            return self._once(req, "lease.renew", cfg, lambda actor: self._renew(req, actor, cfg))

    def _renew(self, req: dict, actor: str, cfg: HubConfig) -> dict:
        from .fold import lease_live
        if True:
            item_id = req.get("item")
            events, state = self._state()
            item = state.items.get(item_id) if isinstance(item_id, str) else None
            if item is None or item.lease_holder != actor or item.status != "claimed":
                raise HubRefused("not_holder", f"{actor} holds no lease on {item_id!r}")
            current = (item.lease_ref or {}).get("attempt")
            if current and req.get("attempt") != current:
                raise HubRefused("wrong_attempt", f"the renewal names attempt {req.get('attempt')!r}; the live "
                                 f"attempt on {item_id} is {current!r}")
            now = self.clock()
            if not lease_live(item, now):
                raise HubRefused("expired", f"the lease on {item_id} expired at {item.lease_expires_ms}; a renewal "
                                 "does not resurrect it -- request a new lease")
            event = self.log.append("item.lease.renew", {
                "id": item_id, "holder": actor, "attempt": current, "renewed_ms": now,
                "expires_ms": now + cfg.lease_ttl_s * 1000,
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
            cfg = self._writable(grant=True)
            events, _ = self._state()
            eps = self._authority(cfg).epochs
            if len(eps) > 1:
                # Every new seal extends the predecessor's frontier (finding 4).
                pred = eps[-2]
                head = next((e for e in events if (getattr(e, "log", None) or e.actor) == pred.grantor
                             and e.seq == pred.fence_seq), None)
                if pred.fence_seq is not None and pred.fence_seq >= 0 and (
                        head is None or head.hash != pred.fence_head):
                    raise HubRefused("behind_predecessor", f"this copy does not hold {pred.grantor}'s frontier "
                                     f"(seq {pred.fence_seq}) that the promotion names; pull it before sealing")
            try:
                # The AUTHORITATIVE state (lease authority, policy): what readers verify.
                payload = build_seal_payload(events, fold_kwargs=self._fold_kwargs(cfg))
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

            def body(actor):
                if actor not in {self._authority(cfg).active.grantor, *cfg.seal_operators}:
                    raise HubRefused("not_operator", f"{actor!r} is not a seal operator of this space")
                return self.seal()
            return self._once(req, "seal.request", cfg, body)

    def publish(self, message: str) -> str:
        """Commit this hub's OWN log (grants, expiries, seals) and nothing else
        (finding 15). Other writers' logs in this checkout are theirs to publish."""
        with self._mutex:
            return publish_own_log(self.space_dir, [self.log.path, self.log.telemetry_path], message)

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
            "frontiers": self.frontiers(),
            "leases": {iid: {"holder": it.lease_holder, "expires_ms": it.lease_expires_ms,
                             "live": lease_live(it, now)}
                       for iid, it in sorted(state.items.items()) if it.lease_holder is not None},
        }

    def presence(self, agents=()) -> dict:
        """Who is checking in (ledger.presence), by the hub's clock."""
        from .presence import presence
        cfg = self.config()
        events, _ = self._state()
        now = self.clock()
        return {"hub": cfg.hub_id, "now_ms": now,
                "agents": presence(events, now, agents=agents, registry_path=self.registry_path)}

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


def request_digest(req: dict) -> str:
    """sha256 of the writer's FULL signed request (signature included)."""
    return hashlib.sha256(canonical_bytes(req)).hexdigest()


def attempt_id(req: dict) -> str:
    """The immutable attempt id a grant answering `req` carries (audit finding 9)."""
    return "att-" + request_digest(req)[:24]


def publish_own_log(space_dir, paths, message: str) -> str:
    """Commit exactly `paths` -- the publisher's OWN log files -- and nothing
    else; return the commit id (amendment H; audit finding 15).

    Git stays the record and the fallback: while the hub is unreachable a
    writer publishes its own signed log this way, and the hub publishes only
    its own log (grants, expiries, seals). It never stages or commits another
    writer's log, never pushes (the existing sync job does, under the
    pre-push own-log gate), and the repository's hooks run as for any commit
    (the ledger write gate checks every new line). A refusal by a hook is
    reported, never bypassed (`publish_failed`)."""
    import subprocess
    space = Path(space_dir)
    top = subprocess.run(["git", "-C", str(space), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode != 0:
        raise HubRefused("publish_failed", f"{space} is not in a git checkout")
    repo = Path(top.stdout.strip())
    rel = [str(Path(p).resolve().relative_to(repo.resolve())) for p in paths if Path(p).exists()]
    if not rel:
        raise HubRefused("nothing_to_publish", "this writer has no log to publish yet")
    for cmd in (["add", "--", *rel], ["commit", "-q", "-m", message, "--", *rel]):
        r = subprocess.run(["git", "-C", str(repo), *cmd], capture_output=True, text=True)
        if r.returncode != 0:
            raise HubRefused("publish_failed", f"git {cmd[0]} refused: {(r.stderr or r.stdout).strip()[:500]}")
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True)
    return head.stdout.strip()


def _request_ref(req: dict) -> dict:
    """What a hub answer records of the request it answers (bound by the writer, findings 5, 9)."""
    return {"kind": req["kind"], "nonce": req["nonce"], "ts_ms": req["ts_ms"], "digest": request_digest(req)}


def _open_attempt(item) -> bool:
    """Has `item` a lease attempt that no `item.lease.expire` has ended yet?"""
    ref = item.lease_ref or {}
    return bool(ref.get("attempt")) and not ref.get("ended")


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

    def presence(self, agents=()) -> dict:
        return self._hub().presence(agents)

    def seal_request(self, req: dict) -> dict:
        return self._hub().seal_request(req)

    def reconcile(self, req: dict) -> dict:
        return self._hub().reconcile(req)


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

    def presence(self, agents=()) -> dict:
        import urllib.parse
        query = "?" + urllib.parse.urlencode({"agents": ",".join(agents)}) if agents else ""
        return self._call("GET", self.prefix + "/presence" + query)

    def seal_request(self, req: dict) -> dict:
        return self._call("POST", self.prefix + "/seal", req)

    def reconcile(self, req: dict) -> dict:
        return self._call("POST", self.prefix + "/reconcile", req)


# ── the writer's side ───────────────────────────────────────────────────────

#: Refusals of an event for what it IS: already in a signed writer log, such an
#: event is kept by the hub as a rejected proposal (finding 11).
REJECTABLE = PROPOSAL_CODES | {"not_authorized"}


class HubWriter:
    """A writer in a hub space: local first, hub second, receipt third.

    Its own per-writer log IS the buffer: while the hub is unreachable every
    append still lands locally (safe without coordination), and `replay` sends
    whatever the hub does not yet hold, in order. Pool leasing pauses: a lease
    needs the hub, and `request_lease` raises `HubUnavailable`.
    """

    def __init__(self, space_dir, actor: str, transport, *, keys_dir=None, registry_path=None,
                 state_dir=None, clock: Callable[[], int] | None = None, log_name: str | None = None,
                 policy=None):
        self.space_dir = Path(space_dir)
        cfg = load_config(self.space_dir)
        if cfg is None:
            raise HubRefused("hub_off", f"{self.space_dir} has not opted in to a hub ({CONFIG})")
        self.cfg, self.actor, self.transport = cfg, actor, transport
        # The same explicit policy input the hub folds with: a receipt recomputes
        # the AUTHORITATIVE sealed state (findings 11, 12). Read once, here.
        from .hub_policy import PolicyInputs
        self.policy = policy if isinstance(policy, PolicyInputs) else PolicyInputs.load(policy)
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
    def _submission(self, e: Event, chain: str | None = None, *, rejected: bool = False) -> dict:
        """What is sent: the event, its log, and the EXACT chain it lies in."""
        from .log import TELEMETRY_SUFFIX, is_telemetry
        if chain is None:
            chain = self.log.log_name + (TELEMETRY_SUFFIX if is_telemetry(e.type, e.payload) else "")
        out = {"event": _event_dict(e), "log": self.log.log_name, "chain": chain}
        if rejected:
            out["rejected_proposal"] = True
        return out

    def _grantor(self) -> str:
        return authority(self.space_dir, load_config(self.space_dir) or self.cfg, self.registry_path).active.grantor

    def _check_statement(self, st, kind: str, code: str) -> dict:
        """A hub-signed statement (append ack, frontier): signed by this space's
        grantor with its current key, for this space and hub."""
        from .keys import verify
        cfg = load_config(self.space_dir) or self.cfg
        if not isinstance(st, dict) or st.get("kind") != kind or st.get("space") != cfg.space_id \
                or st.get("hub") != cfg.hub_id or st.get("signer") != self._grantor() \
                or not isinstance(st.get("sig"), str):
            raise HubRefused(code, f"the hub's {kind} is not a signed statement of this space's grantor")
        body = canonical_bytes({k: v for k, v in st.items() if k != "sig"})
        if not verify(st["signer"], body, st["sig"], registry_path=self.registry_path, at_ms=self.clock()):
            raise HubRefused(code, f"the hub's {kind} signature does not verify")
        return st

    def _send(self, e: Event, chain: str | None = None) -> dict:
        """Submit one local event. One the hub refuses for what it IS, already in
        this signed log, is resubmitted as a rejected proposal so the chain stays
        continuous (finding 11); the refusal is still raised to the caller."""
        try:
            receipt = self.transport.append(self._submission(e, chain))
        except HubRefused as exc:
            if exc.code not in REJECTABLE:
                raise
            receipt = self.transport.append(self._submission(e, chain, rejected=True))
            self._check_ack(receipt, e)
            receipt = {**receipt, "refusal": exc.as_dict()}
            return receipt
        self._check_ack(receipt, e)
        return receipt

    def _check_ack(self, receipt: dict, e: Event) -> None:
        ack = self._check_statement((receipt or {}).get("ack"), "append.ack", "bad_ack")
        if ack.get("seq") != e.seq or ack.get("head") != e.hash:
            raise HubRefused("bad_ack", "the hub's acknowledgment names another frontier")

    def _attempt_for(self, item_id) -> dict | None:
        """The attempt this writer holds on `item_id`: its latest lease receipt."""
        mine = [((r.get("event") or {}).get("payload") or {}) for r in self.receipts() if r.get("item") == item_id]
        for p in reversed(mine):
            if p.get("attempt"):
                # A renewal names the attempt but not the task content: take the
                # task hash from that attempt's own grant (a renewal is not a new pin).
                pinned = next((q.get("task_hash") for q in mine
                               if q.get("attempt") == p["attempt"] and q.get("task_hash")), None)
                return {"attempt": p["attempt"], "task_hash": pinned}
        return None

    def append(self, type: str, payload: dict) -> tuple[Event, dict | None]:
        """Append locally, then submit. Returns (event, receipt or None if buffered).

        A result or release on an item this writer holds a lease on names that
        lease's attempt and task-content hash (audit finding 9), unless the
        caller named them itself."""
        if type in HUB_ONLY_TYPES or (type in OWNER_EVENT_TYPES and self.actor != (self.cfg.owner or None)):
            # Known-invalid operations are refused BEFORE the local append
            # (amendment B): a signed event in my log cannot be taken back.
            raise HubRefused("hub_only_type", f"{type} is written only by the hub or the owner; not appended")
        if type in ("item.complete", "item.release") and isinstance(payload, dict) and "attempt" not in payload:
            held = self._attempt_for(payload.get("id"))
            if held:
                payload = {**payload, **{k: v for k, v in held.items() if v}}
        event = self.log.append(type, payload, after=self._floor())
        try:
            receipt = self._send(event)
            if receipt.get("refusal"):
                r = receipt["refusal"]
                raise HubRefused(r["code"], r.get("message", ""), kept_as="rejected proposal")
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

    def publish(self, message: str) -> str:
        """Commit this writer's own log files, nothing else (finding 15): the git
        path addressed work takes while the hub is unreachable."""
        return publish_own_log(self.space_dir, [self.log.path, self.log.telemetry_path], message)

    def _local_chains(self) -> list[tuple[str, list[Event]]]:
        from .log import TELEMETRY_SUFFIX
        name = self.log.log_name
        return [(name, _parse_file(self.log.path)), (name + TELEMETRY_SUFFIX, _parse_file(self.log.telemetry_path))]

    def replay(self) -> list[dict]:
        """Send every local event the hub does not yet hold, oldest first.

        Nothing is skipped on the hub's word alone (finding 11): the hub's
        frontier for each of my logs is a statement signed by the grantor, and
        my own event at that seq must be exactly the head it names -- the hash
        chain then proves the whole common prefix. A different event at the
        same seq stops reconciliation (`diverged`, for the owner) and the
        outage stays marked until every log has reconciled."""
        try:
            status = self.transport.status()
        except HubUnavailable:
            self._mark_outage()
            raise
        frontier = self._check_statement(status.get("frontiers"), "frontier", "bad_frontier")
        logs = frontier.get("logs") if isinstance(frontier.get("logs"), dict) else {}
        out = []
        for chain, events in self._local_chains():
            held = logs.get(chain) or {"seq": -1, "head": GENESIS}
            mine = {e.seq: e for e in events}
            top = held.get("seq", -1)
            if top >= 0 and (top not in mine or mine[top].hash != held.get("head")):
                self._mark_outage()
                raise HubRefused("diverged", f"the hub holds a different {chain} at seq {top} than this writer "
                                 "signed; reconciliation of this log stops here and goes to the owner",
                                 log=chain, seq=top)
            for e in events:
                if e.seq > top:
                    out.append(self._send(e, chain))
        self._reconcile()
        self._clear_outage()
        return out

    def _reconcile(self) -> dict | None:
        """The writer's half of the recovery barrier (finding 10): present my
        per-log frontier and the hub-signed grants I stored."""
        if not hasattr(self.transport, "reconcile"):
            return None
        frontier = {}
        for chain, events in self._local_chains():
            if events:
                frontier[chain] = {"seq": events[-1].seq, "head": events[-1].hash}
        leases = [r["event"] for r in self.receipts() if (r.get("event") or {}).get("type") == "item.lease"]
        req = sign_request(self.actor, "reconcile", {"hub": self.cfg.hub_id, "space": self.cfg.space_id,
                                                     "frontier": frontier, "leases": leases},
                           keys_dir=self.keys_dir, clock=self.clock)
        return self.transport.reconcile(req)

    # ── leases ─────────────────────────────────────────────────────────────
    def _pending_path(self, kind: str, item_id: str) -> Path:
        return self._receipts_path.parent / f"pending-{kind}-{hashlib.sha256(str(item_id).encode()).hexdigest()[:16]}.json"

    def _pending(self, item_id: str, kind: str = "lease.request") -> dict | None:
        """A request sent whose answer never arrived: retried as is, so the hub
        returns the same signed answer (finding 10). None once answered or stale."""
        try:
            req = json.loads(self._pending_path(kind, item_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        if not isinstance(req, dict) or abs(self.clock() - int(req.get("ts_ms", 0))) > REQUEST_WINDOW_MS:
            return None
        return req

    def _ask(self, method: str, kind: str, item_id: str) -> dict:
        req = self._pending(item_id, kind)
        if req is None:
            body = {"hub": self.cfg.hub_id, "space": self.cfg.space_id, "item": item_id}
            if kind == "lease.renew":
                body["attempt"] = (self._attempt_for(item_id) or {}).get("attempt")
            req = sign_request(self.actor, kind, body, keys_dir=self.keys_dir, clock=self.clock)
        from file_utils import atomic_write_text
        pending = self._pending_path(kind, item_id)
        atomic_write_text(pending, json.dumps(req, sort_keys=True))
        try:
            answer = getattr(self.transport, method)(req)
        except HubUnavailable:
            self._mark_outage()
            raise                                   # the request stays pending: retried as is
        except HubRefused:
            pending.unlink(missing_ok=True)
            raise
        pending.unlink(missing_ok=True)
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
        # The grantor this writer's copy of the OWNER'S RECORDS names (finding 4).
        grantor = authority(self.space_dir, cfg, self.registry_path).active.grantor
        try:
            e = Event(**lease)
        except TypeError:
            raise HubRefused("bad_lease", "the hub's lease answer is not an event; not starting") from None
        payload = e.payload if isinstance(e.payload, dict) else {}
        ref = payload.get("request") if isinstance(payload.get("request"), dict) else {}
        checks = [
            (e.type == expected_type, f"answer is a {e.type!r}, not a {expected_type!r}"),
            (e.actor == grantor, f"answer is signed by {e.actor!r}, not this space's grantor {grantor!r}"),
            (e.hub == cfg.hub_id and e.domain == signing_domain(cfg.space_id, cfg.hub_id, e.actor),
             "answer belongs to another space, hub or log"),
            (payload.get("id") == item_id and payload.get("holder") == self.actor,
             f"answer is not a lease on {item_id} to {self.actor}"),
            (ref.get("nonce") == req["nonce"] and ref.get("kind") == req["kind"]
             and ref.get("digest") == request_digest(req),
             "answer does not answer this request (nonce, digest)"),
            (payload.get("attempt") == (attempt_id(req) if expected_type == "item.lease" else req.get("attempt"))
             and bool(payload.get("attempt")),
             "answer names another lease attempt"),
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
        from .fold import fold
        from .keys import verify
        from .log import read_events
        from .seal import as_sequencer, latest_seal, verify_seal
        from .verify import check_events
        events = read_events(Path(sealed_space))
        problems: list[str] = []
        pending: list[str] = []
        covered: set[str] = set()

        def result(status: str) -> dict:
            return {"status": status, "ok": status == "confirmed", "problems": problems, "pending": pending}

        # Who grants and up to which fence, as the SEALED copy's owner records say (finding 4).
        try:
            scfg = load_config(Path(sealed_space)) or self.cfg
            sauth = authority(Path(sealed_space), scfg, self.registry_path)
        except (HubError, ValueError) as exc:
            problems.append(f"the sealed copy's hub records do not verify: {exc}")
            return result("failed")
        hub_actor = hub_actor or sauth.active.grantor

        # The hub seals its AUTHORITATIVE state; recompute it with the same
        # explicit inputs, over what admission lets in (findings 11, 12).
        fold_kwargs = {"lease_grantors": sauth.lease_grantors(), "policy": self.policy}
        admitted = admit(events, sauth, now_ms=self.clock()).events
        with as_sequencer(hub_actor):
            try:
                seal = latest_seal(admitted)
            except ValueError as exc:
                problems.append(f"unreadable seal: {exc}")
                return result("failed")
            verdict, detail = verify_seal(admitted, fold_kwargs) if seal is not None else (None, "no seal yet")
        if seal is None:
            pending.append(f"no seal by {hub_actor} yet")
        elif verdict is False:
            problems.append(f"the seal does not verify: {detail}")
        elif verdict is None:
            pending.append(f"the seal cannot be verified here yet: {detail}")
        marks = seal.watermarks if seal is not None else {}
        sealer = sauth.epoch_of(hub_actor)
        if seal is not None and sealer is not None and sealer.epoch > 0:
            pred = sauth.epochs[sealer.epoch - 1]
            if pred.fence_seq is not None and marks.get(pred.grantor, -1) < pred.fence_seq:
                problems.append(f"the seal does not extend {pred.grantor}'s frontier (seq {pred.fence_seq})")
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
            ep = sauth.epoch_of(r["log"])
            if ep is not None and ep.fence_seq is not None and lease.seq > ep.fence_seq:
                problems.append(f"lease on {r['item']} (hub seq {lease.seq}) was granted after {r['log']} was "
                                f"fenced at seq {ep.fence_seq}: it confers nothing")
                continue
            s = sealed.get(r["log"], {}).get(lease.seq)
            if s is not None and (s.hash != lease.hash or to_line(s) != to_line(lease)):
                problems.append(f"lease on {r['item']} (hub seq {lease.seq}) does not match the sealed log")
            elif lease.seq > marks.get(r["log"], -1):
                pending.append(f"lease on {r['item']} (hub seq {lease.seq}) not sealed yet")
            elif s is None:
                problems.append(f"lease on {r['item']} (hub seq {lease.seq}) is missing from the sealed log")
            else:
                covered.add(lease.hash)
        # THE SEALED DECISION MUST AUTHORIZE THE OPERATION (part-1 leftover): a
        # covered event that the authoritative fold turned into a lease
        # conflict or an owner-resolution case is not a confirmation.
        if seal is not None and verdict is True:
            settled = fold([e for e in admitted if seal.includes(e)], **fold_kwargs)
            for it in settled.items.values():
                for h in covered & (set(it.lease_conflicts) | set(it.lease_unresolved)):
                    why = it.lease_conflicts.get(h) or it.lease_unresolved.get(h)
                    problems.append(f"{it.id}: the sealed decision does not authorize {h[:12]} ({why})")
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
    en.add_argument("--owner", required=True, help="the owner who signs the hub.enable record")
    en.add_argument("--lease-ttl-s", type=int, default=DEFAULT_LEASE_TTL_S)
    en.add_argument("--clock-uncertainty-ms", type=int, default=DEFAULT_CLOCK_UNCERTAINTY_MS)
    pr = sub.add_parser("promote", help="move lease granting to another hub (owner; never automatic)")
    pr.add_argument("space")
    pr.add_argument("grantor")
    pr.add_argument("--by", required=True)
    pr.add_argument("--reason", required=True)
    pr.add_argument("--attest-stopped", action="store_true",
                    help="the old grantor cannot fence itself; the owner states it is stopped")
    rd = sub.add_parser("ready", help="this writer's host speaks the hub protocol (run before `enable`)")
    rd.add_argument("space")
    rd.add_argument("--actor", required=True)
    fe = sub.add_parser("fence", help="this grantor stops granting (run on the old hub before a promotion)")
    fe.add_argument("space")
    fe.add_argument("--actor", required=True)
    fe.add_argument("--reason", required=True)
    sh = sub.add_parser("show", help="print a space's hub config")
    sh.add_argument("space")
    a = ap.parse_args(argv)
    if a.cmd == "enable":
        cfg = enable(a.space, hub_id=a.hub_id, grantor=a.grantor, owner=a.owner, lease_ttl_s=a.lease_ttl_s,
                     clock_uncertainty_ms=a.clock_uncertainty_ms)
    elif a.cmd == "promote":
        cfg = promote(a.space, a.grantor, by=a.by, reason=a.reason, attest_stopped=a.attest_stopped)
    elif a.cmd == "ready":
        print(json.dumps(declare_ready(a.space, a.actor), indent=2, sort_keys=True))
        return 0
    elif a.cmd == "fence":
        hub = Hub(a.space, a.actor)
        try:
            print(json.dumps(hub.fence(a.reason), indent=2, sort_keys=True))
        finally:
            hub.close()
        return 0
    else:
        cfg = load_config(a.space)
    print(json.dumps(asdict(cfg) if cfg else {"enabled": False}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
