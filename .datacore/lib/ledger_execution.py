"""One durable execution per installation-bound delegation identity.

The replicated ledger remains an eventually consistent record. The core
execution authority supplies admission outside Data; workers must not possess
its controller identity. There is no automatic retry or ownership transfer.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import uuid

import execution_admission as authority
from ledger.fold import fold
from ledger.log import EventLog, read_events
from ledger.policy import approval_payload_hash, guarded_append
from spaces import read_marker


def delegation_id(title: str, space: str, installation: str) -> str:
    """Bind the execution target into the identity, including across hosts."""
    authority.installation_id(installation)
    if any(not isinstance(x, str) or not x.strip() for x in (title, space)):
        raise authority.SiteError('delegation title and canonical space are required')
    raw = json.dumps([' '.join(title.lower().split()), space, installation],
                     separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return 'delegation-' + hashlib.sha256(raw).hexdigest()


def execution_space(path: Path) -> str:
    marker = read_marker(path, strict=True)
    if marker is None:
        raise authority.SiteError('execution requires a canonical space marker')
    return marker['name']


@dataclass(frozen=True)
class Receipt:
    space: Path
    item: str
    owner: str
    installation: str
    token: str
    payload_hash: str

    def valid(self) -> bool:
        """Recheck the controller and exact claim before protected completion."""
        site = authority.load()
        if (site.installation != self.installation or
                not site.owns(self.item, self.owner, self.token, self.payload_hash)):
            return False
        current = fold(read_events(self.space)).items.get(self.item)
        return bool(current and current.status == 'claimed' and
                    not current.edit_conflicts and current.owner == self.owner and
                    current.claimed_payload_hash == self.payload_hash and
                    approval_payload_hash(current.payload) == self.payload_hash and
                    execution_space(self.space) == current.payload.get('execution_space'))

    def record(self, event_type: str, detail: dict):
        if event_type not in ('item.complete', 'item.verify'):
            raise authority.SiteError('unsupported execution result event')
        event = guarded_append(EventLog(self.space, self.owner), event_type,
                               {**detail, 'id': self.item, 'owner': self.owner,
                                'execution_token': self.token, 'payload_hash': self.payload_hash})
        if event_type == 'item.complete':
            item = fold(read_events(self.space)).items.get(self.item)
            if not item or item.status != 'completed' or item.owner != self.owner or item.closed_at != event.hlc:
                raise authority.SiteError('execution completion was not acknowledged; reconcile before retry')
        return event


def validate_result(log, payload, item):
    """Called under the canonical policy lock for every bound result path."""
    site = authority.load()
    token = payload.get('execution_token')
    digest = approval_payload_hash(item.payload)
    if (item.status != 'claimed' or item.edit_conflicts or item.owner != log.actor or
            payload.get('owner') != log.actor or item.claimed_payload_hash != digest or
            payload.get('payload_hash') != digest or not isinstance(token, str) or
            item.payload.get('execution_installation') != site.installation or
            execution_space(log.space_dir) != item.payload.get('execution_space') or
            not site.owns(item.id, log.actor, token, digest)):
        raise authority.SiteError('execution result does not own the current admitted delegation')


def admit(space: Path, selected, actor: str, route: str, reason: str) -> Receipt:
    """Validate an immutable creation binding, claim, then durably consume it."""
    site = authority.load()
    events = read_events(space)
    current = fold(events).items.get(selected.id)
    if (current is None or current.status != 'created' or current.edit_conflicts or
            approval_payload_hash(current.payload) != approval_payload_hash(selected.payload)):
        raise authority.SiteError('delegation changed or is unavailable; select it again')
    creations = {e.hash: e.payload for e in events
                 if e.type == 'item.create' and e.payload.get('id') == current.id}
    if len(creations) != 1:
        raise authority.SiteError('delegation creation is ambiguous; reconcile before execution')
    original = next(iter(creations.values()))
    installation = original.get('execution_installation')
    scope = original.get('execution_space')
    if (installation != site.installation or scope != execution_space(space) or
            any(current.payload.get(k) != original.get(k)
                for k in ('execution_installation', 'execution_space')) or
            delegation_id(original.get('title'), scope, installation) != current.id):
        raise authority.SiteError('delegation has no valid binding to this execution installation')
    if any(e.payload.get('id') == current.id and e.type in ('item.claim', 'item.release')
           for e in events):
        raise authority.SiteError('delegation was already attempted; reconcile before authorizing new work')
    digest = approval_payload_hash(current.payload)
    token = uuid.uuid4().hex
    # A claim remains visible if admission or a later instruction fails. A
    # stale Data copy cannot reset consumption, even if its claim is absent.
    guarded_append(EventLog(space, actor), 'item.claim',
                   {'id': current.id, 'owner': actor, 'route': route, 'reason': reason,
                    'payload_hash': digest, 'execution_token': token})
    site.consume(current.id, actor, token, digest)
    receipt = Receipt(space, current.id, actor, installation, token, digest)
    if not receipt.valid():
        raise authority.SiteError('delegation ownership changed before execution; reconcile before retry')
    return receipt
